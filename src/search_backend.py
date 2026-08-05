"""
Hybrid search backend for Engram — SQLite FTS5 + local semantic embeddings.

Full-text search with Porter stemming, graph relation indexing, and tag
filtering (via SQLite's built-in sqlite3 module), fused with cosine
similarity over Model2Vec static embeddings (local, no cloud dependency)
using Reciprocal Rank Fusion. The database is a rebuildable cache on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from model2vec import StaticModel

from config import (
    CANDIDATE_FRACTION_DIVISOR,
    CLICK_THROUGH_WINDOW_MINUTES,
    DEAD_ENTRY_STALE_DAYS,
    DEFAULT_EMBEDDING_MODEL,
    MIN_COSINE_SIMILARITY,
    MIN_EXACT_DISCRIMINATING_TOKENS,
    MIN_EXACT_TOKEN_LEN,
    QUERY_LOG_TEXT_TRUNCATE,
    RRF_K,
    STALENESS_HALF_LIFE_DAYS,
    SUGGESTION_MIN_SIMILARITY,
    SUGGESTION_TOP_K,
    WRITE_GATE_CANDIDATES,
    WRITE_GATE_MIN_SIMILARITY,
    ZERO_HIT_QUERIES_LIMIT,
)
from schema import DEFAULT_EDGE, DEFAULT_EDGES

logger = logging.getLogger("engram")

_model_cache: dict[str, StaticModel] = {}


def _get_model(model_name: str) -> StaticModel | None:
    """
    Load (and cache) the Model2Vec static embedding model.

    Args:
        model_name: HuggingFace hub id of the model to load.

    Returns:
        The loaded StaticModel, or None if it could not be loaded
        (e.g. no network and not yet cached) — callers must degrade
        to BM25-only search in that case.
    """

    if model_name in _model_cache:
        return _model_cache[model_name]

    try:
        model = StaticModel.from_pretrained(model_name)
    except Exception as exc:
        logger.warning("Embedding model '%s' unavailable: %s", model_name, exc)
        return None

    _model_cache[model_name] = model
    return model

# Regex for extracting kb:// links: kb://uuid, kb://uuid#type, or
# kb://uuid#type:edge — the fragment names the target entry's type, the
# optional :edge suffix names the relationship's semantics.
RE_KB_LINK: re.Pattern[str] = re.compile(
    r"\[[^\]]*\]\(kb://([a-f0-9-]+)(?:#([a-zA-Z0-9_-]+?)(?::([a-z_]+))?)?\)"
)

# Allowed edge semantics on kb:// links, used when no vocabulary is handed
# in. The real one comes from the schema (`edges`), which KnowledgeBase
# pushes onto the backend — this is the fallback for callers parsing links
# without a loaded schema.
EDGE_TYPES: frozenset[str] = frozenset(DEFAULT_EDGES)

# Edge semantics assumed when a link carries no :edge suffix
DEFAULT_EDGE_TYPE: str = DEFAULT_EDGE

# ---------------------------------------------------------------------------
# Relation extraction (shared utility)
# ---------------------------------------------------------------------------


def extract_relations(
    content: str, allowed_edges: frozenset[str] = EDGE_TYPES
) -> list[dict[str, str]]:
    """
    Extract kb:// link relations from Markdown content.

    Parses links of the form [label](kb://uuid), [label](kb://uuid#type),
    or [label](kb://uuid#type:edge). When no #type fragment is present,
    type defaults to "related"; when no :edge suffix is present (or the
    edge is not allowed), edge defaults to DEFAULT_EDGE_TYPE.

    Args:
        content: Markdown content body.
        allowed_edges: Edge vocabulary to validate against — the schema's
            `edges`. Defaults to EDGE_TYPES for callers without a schema.

    Returns:
        List of dicts with 'target' (UUID), 'type' (target entry type),
        and 'edge' (relationship semantics).
    """

    relations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in RE_KB_LINK.finditer(content):
        target_id = match.group(1)
        rel_type = match.group(2) or "related"
        edge = match.group(3) or DEFAULT_EDGE_TYPE
        if edge not in allowed_edges:
            logger.warning(
                "Unknown edge type '%s' on kb://%s — treating as %s",
                edge,
                target_id,
                DEFAULT_EDGE_TYPE,
            )
            edge = DEFAULT_EDGE_TYPE

        # Deduplicate identical target+type+edge triples
        key = (target_id, rel_type, edge)
        if key in seen:
            continue
        seen.add(key)

        relations.append({"target": target_id, "type": rel_type, "edge": edge})

    return relations


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_SQL_CREATE_ENTRIES: str = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    tags TEXT NOT NULL,  -- JSON array
    type TEXT DEFAULT '',
    resource TEXT DEFAULT '',
    part_of TEXT DEFAULT '[]',  -- JSON array of hub UUIDs
    embedding BLOB,
    content TEXT DEFAULT '',
    content_hash TEXT DEFAULT '',
    access_count INTEGER DEFAULT 0,
    last_accessed TEXT DEFAULT ''
)
"""

# External-content table (content='entries') rather than genuinely
# contentless (content='') — contentless FTS5 tables reject any DELETE
# once a row has real indexed data, which broke unindex() and (via a
# separate rowid-reuse issue on `entries`, a TEXT-keyed table where
# INSERT OR REPLACE always assigns a fresh rowid) silently orphaned a
# duplicate row on every update. External-content DELETE works because
# FTS5 reads the current row back from `entries` to know what to remove.
_SQL_CREATE_FTS: str = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, content, tags,
    content='entries',
    content_rowid='rowid',
    tokenize='porter unicode61'
)
"""

_SQL_CREATE_RELATIONS: str = """
CREATE TABLE IF NOT EXISTS relations (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'related',
    edge_type TEXT NOT NULL DEFAULT 'related_to',
    PRIMARY KEY (source_id, target_id, type, edge_type)
)
"""

# The PK covers source_id lookups (outgoing links) but leaves every
# incoming-link query — get_relations, the hub digest, doctor — a full
# table scan.
_SQL_CREATE_RELATIONS_TARGET_INDEX: str = """
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id)
"""

# One row per search/recall/remember tool call — the usage-analytics trace
# behind /api/analytics. Distinct from the optional ENGRAM_QUERY_LOG JSONL
# file (log_query() in server.py), which keeps feeding
# scripts/eval_retrieval.py unchanged; this is a separate write path, not a
# replacement, and deliberately does not touch access_count/last_accessed
# (see the rich-get-richer fix on search hits).
_SQL_CREATE_QUERY_LOG: str = """
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    query_text TEXT,
    entry_type TEXT,
    returned_ids TEXT,
    top_result_id TEXT,
    entry_id TEXT,
    hit INTEGER,
    latency_ms INTEGER
)
"""

# Every session-scoped analytics query (searches-per-recall, average recall
# rank, click-through) groups by session_id and orders by ts.
_SQL_CREATE_QUERY_LOG_INDEX: str = """
CREATE INDEX IF NOT EXISTS idx_query_log_session_ts ON query_log(session_id, ts)
"""


class SQLiteBackend:
    """
    SQLite FTS5 search backend with Porter stemming.

    Stores a full-text index in a single SQLite database file. The database
    is a cache — Markdown files are the source of truth and can be rebuilt
    at any time.

    Args:
        db_path: Path to the SQLite database file.
        embedding_model: HuggingFace hub id of the Model2Vec model to use
            for semantic search. Defaults to ENGRAM_EMBEDDING_MODEL env
            var, falling back to DEFAULT_EMBEDDING_MODEL.
    """

    def __init__(
        self,
        db_path: str | Path,
        embedding_model: str | None = None,
        no_boost_types: frozenset[str] = frozenset(),
        allowed_edges: frozenset[str] = EDGE_TYPES,
    ) -> None:
        """
        Initialize the SQLite backend and create tables if needed.

        Opens the database in WAL mode for concurrent read access.

        Args:
            db_path: Path to the SQLite database file.
            embedding_model: HuggingFace hub id of the Model2Vec model.
            no_boost_types: Entry types exempt from the usage boost in
                search ranking (the schema's `usage_boost: false` types).
                Which types those are is a schema decision, not a
                backend one — KnowledgeBase, which owns the schema, sets
                this on whatever backend it is given, so callers
                constructing a backend by hand need not pass it.
            allowed_edges: kb:// edge vocabulary (the schema's `edges`).
                Set by KnowledgeBase for the same reason as
                no_boost_types; defaults to EDGE_TYPES.

        Errors:
            Creates parent directories if missing.
        """

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_model = embedding_model or os.environ.get(
            "ENGRAM_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        )
        self._no_boost_types = no_boost_types
        self._allowed_edges = allowed_edges

        self._ensure_schema()

    @property
    def no_boost_types(self) -> frozenset[str]:
        """Entry types exempt from the usage boost in search ranking."""

        return self._no_boost_types

    @no_boost_types.setter
    def no_boost_types(self, types: frozenset[str]) -> None:
        """
        Declare which types skip the usage boost.

        Args:
            types: Type names from the schema's `usage_boost: false` rules.
        """

        self._no_boost_types = types

    @property
    def allowed_edges(self) -> frozenset[str]:
        """The kb:// edge vocabulary relation extraction validates against."""

        return self._allowed_edges

    @allowed_edges.setter
    def allowed_edges(self, edges: frozenset[str]) -> None:
        """
        Declare which edge semantics kb:// links may carry.

        Args:
            edges: Edge names from the schema's `edges` list.
        """

        self._allowed_edges = edges

    def warm_up(self) -> None:
        """
        Eagerly load the embedding model into the process-lifetime cache.

        Call once at startup so the CPU/memory cost of loading model
        weights happens during boot instead of on the first search().
        """

        _get_model(self._embedding_model)

    def embed(self, text: str) -> bytes | None:
        """
        Compute a Model2Vec embedding for arbitrary text (public entry point).

        For callers outside this module (e.g. KnowledgeBase's link-suggestion
        pass) that need an embedding without going through index()/search().

        Args:
            text: Text to embed.

        Returns:
            Raw float32 bytes of the embedding vector, or None if the
            embedding model is unavailable.
        """

        return self._embed_text(text)

    def _content_hash(self, title: str, content: str) -> str:
        """
        Content-addressed cache key for an entry's embedding.

        Includes the embedding model id so switching models invalidates
        every cached embedding automatically.

        Args:
            title: Entry title.
            content: Entry body.

        Returns:
            Hex sha256 digest.
        """

        payload = f"{self._embedding_model}\n{title}\n{content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _embed_text(self, text: str) -> bytes | None:
        """
        Compute a Model2Vec embedding for text, serialized as float32 bytes.

        Args:
            text: Text to embed.

        Returns:
            Raw float32 bytes of the embedding vector, or None if the
            embedding model is unavailable.
        """

        model = _get_model(self._embedding_model)
        if model is None:
            return None

        vector = model.encode([text])[0].astype(np.float32)
        return vector.tobytes()

    # -------------------------------------------------------------------
    # Internal database access
    # -------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """
        Open a connection to the SQLite database with WAL mode.

        Returns:
            A sqlite3.Connection instance with WAL journal mode enabled.
        """

        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row

        return conn

    def _ensure_schema(self) -> None:
        """
        Create tables and FTS virtual table if they do not exist.

        Runs all CREATE IF NOT EXISTS statements in a single transaction.
        """

        conn = self._connect()
        try:
            conn.execute(_SQL_CREATE_ENTRIES)

            # Migrate pre-existing databases that predate the embedding column
            try:
                conn.execute("ALTER TABLE entries ADD COLUMN embedding BLOB")
            except sqlite3.OperationalError:
                pass

            # Migrate pre-existing databases that predate the content column
            # (needed for entries_fts to work as an external-content table)
            try:
                conn.execute("ALTER TABLE entries ADD COLUMN content TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

            # Migrate pre-existing databases that predate the embedding cache
            try:
                conn.execute(
                    "ALTER TABLE entries ADD COLUMN content_hash TEXT DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass

            # Migrate pre-existing databases that predate usage tracking
            # and structural membership
            for ddl in (
                "ALTER TABLE entries ADD COLUMN access_count INTEGER DEFAULT 0",
                "ALTER TABLE entries ADD COLUMN last_accessed TEXT DEFAULT ''",
                "ALTER TABLE entries ADD COLUMN part_of TEXT DEFAULT '[]'",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

            # Migrate a legacy contentless entries_fts (content='') to the
            # external-content form — its module arguments can't be ALTERed,
            # so the old table is dropped and recreated. This drops its
            # indexed text; callers must run rebuild() once afterward to
            # repopulate the full-text index from the Markdown entries
            # (the SQLite index is always a rebuildable cache, never the
            # source of truth).
            legacy_fts = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries_fts'"
            ).fetchone()
            if legacy_fts is not None and "content='entries'" not in (legacy_fts[0] or ""):
                conn.execute("DROP TABLE entries_fts")
                logger.warning(
                    "Migrated entries_fts to external-content FTS5 — "
                    "run rebuild() to repopulate the full-text index"
                )

            conn.execute(_SQL_CREATE_FTS)

            # Migrate a legacy relations table without edge_type — the PK
            # changes, so drop and recreate. Relations are derived from
            # entry content; rebuild() repopulates them.
            legacy_relations = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='relations'"
            ).fetchone()
            if legacy_relations is not None and "edge_type" not in (
                legacy_relations[0] or ""
            ):
                conn.execute("DROP TABLE relations")
                logger.warning(
                    "Migrated relations table to typed edges — "
                    "run rebuild() to repopulate the relation graph"
                )

            conn.execute(_SQL_CREATE_RELATIONS)
            conn.execute(_SQL_CREATE_RELATIONS_TARGET_INDEX)

            conn.execute(_SQL_CREATE_QUERY_LOG)
            conn.execute(_SQL_CREATE_QUERY_LOG_INDEX)

            conn.commit()
        finally:
            conn.close()

        logger.info("SQLite schema ensured at %s", self._db_path)

    # -------------------------------------------------------------------
    # Internal indexing helpers
    # -------------------------------------------------------------------

    def _index_entry_with_conn(
        self,
        entry: dict[str, Any],
        conn: sqlite3.Connection,
        embedding: bytes | None = None,
    ) -> None:
        """
        Index or update an entry in a pre-opened connection.

        Does NOT commit — the caller is responsible for committing.

        Args:
            entry: Dict with id, title, tags, content.
            conn: An already-open sqlite3.Connection instance.
            embedding: Precomputed embedding bytes (e.g. from a batch
                encode in rebuild()). When None, computed here from
                title + content — one model call per entry.
        """

        entry_id = entry["id"]
        title = entry["title"]
        content = entry["content"]
        tags = entry["tags"]
        entry_type = entry.get("type", "")
        resource = entry.get("resource", "")
        part_of = entry.get("part_of", [])
        part_of_json = json.dumps(part_of, ensure_ascii=False)
        tags_json = json.dumps(tags, ensure_ascii=False)
        # Flatten tags to a space-separated string for FTS indexing.
        # Hub titles ride along (part_of_titles, resolved by the caller):
        # membership is stored as UUIDs, and without the titles a member
        # entry would stop matching queries that name its project.
        part_of_titles = [t for t in entry.get("part_of_titles", []) if t]
        tags_text = " ".join(tags + part_of_titles)
        content_hash = self._content_hash(title, content)

        if embedding is None:
            # Reuse the stored embedding when the text hasn't changed —
            # e.g. a supersede rewrites the old entry's frontmatter only.
            cached = conn.execute(
                "SELECT embedding FROM entries "
                "WHERE id = ? AND content_hash = ? AND embedding IS NOT NULL",
                (entry_id, content_hash),
            ).fetchone()
            if cached is not None:
                embedding = cached[0]
            else:
                embedding = self._embed_text(f"{title}\n{content}")

        # Remove any existing FTS row BEFORE overwriting the entries row.
        # entries_fts is an external-content table (reads `entries` back by
        # rowid to know what to remove), and `entries.id` is a TEXT primary
        # key — not a rowid alias — so INSERT OR REPLACE always assigns a
        # fresh rowid. Deleting first, while `entries` still holds the
        # previous row at its original rowid, is what makes the delete
        # actually match instead of silently targeting a stale rowid.
        # Usage counters are read here too — INSERT OR REPLACE would
        # otherwise reset them to defaults on every re-index.
        access_count = 0
        last_accessed = ""
        old_row = conn.execute(
            "SELECT rowid, access_count, last_accessed FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if old_row is not None:
            conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (old_row[0],))
            access_count = old_row["access_count"]
            last_accessed = old_row["last_accessed"]

        # Upsert into entries table (may assign a new rowid — see above)
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(id, title, tags, type, resource, part_of, embedding, content, "
            "content_hash, access_count, last_accessed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                title,
                tags_json,
                entry_type,
                resource,
                part_of_json,
                embedding,
                content,
                content_hash,
                access_count,
                last_accessed,
            ),
        )

        # Insert into FTS using the (possibly new) rowid from the entries table
        conn.execute(
            "INSERT INTO entries_fts (rowid, title, content, tags) "
            "VALUES ((SELECT rowid FROM entries WHERE id = ?), ?, ?, ?)",
            (entry_id, title, content, tags_text),
        )

        # Extract and store relations from kb:// links
        relations = extract_relations(content, self._allowed_edges)

        # Remove old relations for this source
        conn.execute("DELETE FROM relations WHERE source_id = ?", (entry_id,))

        # Insert new relations
        for rel in relations:
            conn.execute(
                "INSERT OR IGNORE INTO relations "
                "(source_id, target_id, type, edge_type) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, rel["target"], rel["type"], rel["edge"]),
            )

        logger.info("Indexed entry %s", entry_id)

    # -------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------

    def index(self, entry: dict[str, Any]) -> None:
        """
        Index or update a single entry in SQLite.

        Args:
            entry: Dict with id, title, tags, content.
        """

        conn = self._connect()
        try:
            self._index_entry_with_conn(entry, conn)
            conn.commit()
        finally:
            conn.close()

    def unindex(self, entry_id: str) -> None:
        """
        Remove an entry from all SQLite tables.

        Args:
            entry_id: UUID of the entry.
        """

        conn = self._connect()
        try:
            # Delete the FTS row first, while `entries` still has the row at
            # its current rowid — external-content FTS5 reads `entries` back
            # by rowid to know what to remove from the index.
            old_row = conn.execute(
                "SELECT rowid FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if old_row is not None:
                conn.execute("DELETE FROM entries_fts WHERE rowid = ?", (old_row[0],))

            # Delete relations (both directions)
            conn.execute("DELETE FROM relations WHERE source_id = ?", (entry_id,))
            conn.execute("DELETE FROM relations WHERE target_id = ?", (entry_id,))

            # Delete from entries table
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

            conn.commit()
        finally:
            conn.close()

        logger.info("Unindexed entry %s", entry_id)

    @staticmethod
    def _tag_filter_clause(
        tags: list[str] | None, alias: str = "e"
    ) -> tuple[str, list[str]]:
        """
        Build a SQL AND-clause requiring each tag to be present in tags JSON.

        Args:
            tags: Optional list of normalized tags to filter by (AND logic).
            alias: Table alias qualifying the `tags` column.

        Returns:
            Tuple of (SQL fragment starting with "AND ...", params) — the
            SQL fragment is empty and params is [] when tags is falsy.
        """

        if not tags:
            return "", []

        conditions = []
        params: list[str] = []
        for tag in tags:
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}.tags) WHERE value = ?)"
            )
            params.append(tag)

        return "AND " + " AND ".join(conditions), params

    @staticmethod
    def _part_of_filter_clause(
        part_of: list[str] | None, alias: str = "e"
    ) -> tuple[str, list[str]]:
        """
        Build a SQL AND-clause requiring each hub id in the part_of JSON.

        Args:
            part_of: Optional list of hub UUIDs to require (AND logic).
            alias: Table alias qualifying the `part_of` column.

        Returns:
            Tuple of (SQL fragment starting with "AND ...", params) — the
            SQL fragment is empty and params is [] when part_of is falsy.
        """

        if not part_of:
            return "", []

        conditions = []
        params: list[str] = []
        for target in part_of:
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}.part_of) WHERE value = ?)"
            )
            params.append(target)

        return "AND " + " AND ".join(conditions), params

    @staticmethod
    def _type_filter_clause(
        entry_type: str | None, alias: str = "e"
    ) -> tuple[str, list[str]]:
        """
        Build a SQL AND-clause requiring an exact match on entry type.

        Args:
            entry_type: Optional entry type to filter by (exact match).
            alias: Table alias qualifying the `type` column.

        Returns:
            Tuple of (SQL fragment starting with "AND ...", params) — the
            SQL fragment is empty and params is [] when entry_type is falsy.
        """

        if not entry_type:
            return "", []

        return f"AND {alias}.type = ?", [entry_type]

    def _bm25_search(
        self,
        conn: sqlite3.Connection,
        query_str: str,
        tags: list[str] | None,
        limit: int,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[str]:
        """
        Rank entries by BM25 keyword match.

        Args:
            conn: An already-open sqlite3.Connection instance.
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of ids to return.
            entry_type: Optional entry type to filter by (exact match).
            part_of: Optional hub UUIDs to filter by (AND logic).

        Returns:
            List of entry ids, best match first. Empty on FTS5 syntax
            errors — keyword search degrading is not fatal once it's
            just one of two signals feeding fusion.
        """

        # Quote each word as its own FTS5 phrase so operator characters in
        # user input (?, *, (, ), :, ^, -, AND/OR/NOT/NEAR...) are treated
        # as literal text instead of MATCH syntax.
        tokens = re.findall(r"\w+", query_str, flags=re.UNICODE)
        if not tokens:
            # Nothing left to search on keyword side — vector search alone.
            return []
        quoted = [f'"{token}"' for token in tokens]
        tag_where, tag_params = self._tag_filter_clause(tags)
        type_where, type_params = self._type_filter_clause(entry_type)
        part_of_where, part_of_params = self._part_of_filter_clause(part_of)

        sql = (
            "SELECT e.id, bm25(entries_fts) AS score "
            "FROM entries_fts AS f "
            "JOIN entries AS e ON e.rowid = f.rowid "
            f"WHERE entries_fts MATCH ? {tag_where} {type_where} {part_of_where} "
            "ORDER BY score "
            "LIMIT ?"
        )
        filter_params = [*tag_params, *type_params, *part_of_params]

        try:
            cursor = conn.execute(
                sql, [" ".join(quoted), *filter_params, limit]
            )
            ids = [row["id"] for row in cursor]
            # Adjacent phrases are an implicit AND — a long natural-language
            # query with a single term absent from the KB matches nothing.
            # Retry as OR so partial keyword overlap still contributes a
            # ranked list to fusion.
            if not ids and len(quoted) > 1:
                cursor = conn.execute(
                    sql, [" OR ".join(quoted), *filter_params, limit]
                )
                ids = [row["id"] for row in cursor]
            return ids
        except sqlite3.OperationalError as exc:
            logger.warning("BM25 search error for '%s': %s", query_str, exc)
            return []

    def _exact_match_search(
        self,
        conn: sqlite3.Connection,
        query_str: str,
        tags: list[str] | None,
        limit: int,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[str]:
        """
        Rank entries by IDF-weighted literal query-token overlap with
        title/tags.

        BM25 and cosine both approximate — on a large corpus a proper
        noun in the query (a project/product name) gets diluted among
        many lexically-similar distractors sharing generic vocabulary,
        even though an entry whose title literally contains that name
        is the obvious answer. This channel gives such entries a signal
        neither approximate one provides, by matching query tokens as
        literal substrings of the entry's title or tags.

        Each token is weighted by 1/df(token) — the same corpus can have
        one entry whose title is the *only* one containing a rare token
        (should count a lot) alongside a token that names an entire
        project spanning dozens of entries (should barely help pick one
        of them out). An unweighted count degrades to exactly the
        dilution problem this channel exists to fix, just one level
        down: ties among every same-project entry, broken by arbitrary
        SQL row order — confirmed as a regression (RU-query MRR 0.750 ->
        0.438) before this weighting was added.

        Args:
            conn: An already-open sqlite3.Connection instance.
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of ids to return.
            entry_type: Optional entry type to filter by (exact match).
            part_of: Optional hub UUIDs to filter by (AND logic).

        Returns:
            List of entry ids, highest weighted overlap first. Empty
            when the query has no tokens at or above MIN_EXACT_TOKEN_LEN,
            or none of them match any entry.
        """

        tokens = sorted(
            {
                token.lower()
                for token in re.findall(r"\w+", query_str, flags=re.UNICODE)
                if len(token) >= MIN_EXACT_TOKEN_LEN
            }
        )
        if not tokens:
            return []

        tag_where, tag_params = self._tag_filter_clause(tags)
        type_where, type_params = self._type_filter_clause(entry_type)
        part_of_where, part_of_params = self._part_of_filter_clause(part_of)
        filter_params = [*tag_params, *type_params, *part_of_params]

        # Document frequency per token (within the same filters) — one
        # small COUNT query per token, bounded by the query's own word
        # count, not corpus size.
        weights: dict[str, float] = {}
        for token in tokens:
            like = f"%{token}%"
            df = conn.execute(
                "SELECT COUNT(*) FROM entries e "
                "WHERE (lower(e.title) LIKE ? OR lower(e.tags) LIKE ?) "
                f"{tag_where} {type_where} {part_of_where}",
                [like, like, *filter_params],
            ).fetchone()[0]
            if df > 0:
                weights[token] = 1.0 / df

        if len(weights) < MIN_EXACT_DISCRIMINATING_TOKENS:
            # A single matching token can't discriminate between the
            # entries that share it — it ties every one of them at the
            # same score, then SQL row order (not relevance) decides.
            # Confirmed as a real regression: queries whose other tokens
            # don't literally appear anywhere in title/tags (translated
            # queries against untranslated titles are the common case,
            # but any single-clue query degrades the same way) reduce to
            # exactly this, and BM25/vector still cover the single-token
            # case on their own — so this channel abstains rather than
            # add noise.
            return []

        score_terms = " + ".join(
            "(CASE WHEN lower(e.title) LIKE ? OR lower(e.tags) LIKE ? "
            "THEN ? ELSE 0 END)"
            for _ in weights
        )
        match_params: list[Any] = []
        for token, weight in weights.items():
            like = f"%{token}%"
            match_params.extend([like, like, weight])

        sql = (
            "SELECT id FROM ("
            f"SELECT e.id AS id, ({score_terms}) AS match_score FROM entries e "
            f"WHERE 1=1 {tag_where} {type_where} {part_of_where}"
            ") WHERE match_score > 0 "
            "ORDER BY match_score DESC "
            "LIMIT ?"
        )
        cursor = conn.execute(
            sql,
            [*match_params, *tag_params, *type_params, *part_of_params, limit],
        )
        return [row["id"] for row in cursor]

    def _vector_search(
        self,
        conn: sqlite3.Connection,
        query_str: str,
        tags: list[str] | None,
        limit: int,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[str]:
        """
        Rank entries by cosine similarity of their stored embeddings.

        Args:
            conn: An already-open sqlite3.Connection instance.
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of ids to return.
            entry_type: Optional entry type to filter by (exact match).
            part_of: Optional hub UUIDs to filter by (AND logic).

        Returns:
            List of entry ids, most similar first. Empty when the
            embedding model is unavailable or no entries have embeddings.
        """

        query_embedding = self._embed_text(query_str)
        if query_embedding is None:
            return []

        tag_where, tag_params = self._tag_filter_clause(tags)
        type_where, type_params = self._type_filter_clause(entry_type)
        part_of_where, part_of_params = self._part_of_filter_clause(part_of)
        sql = (
            "SELECT id, embedding FROM entries e "
            f"WHERE embedding IS NOT NULL {tag_where} {type_where} {part_of_where}"
        )
        cursor = conn.execute(sql, [*tag_params, *type_params, *part_of_params])

        ids: list[str] = []
        vectors: list[np.ndarray] = []
        for row in cursor:
            ids.append(row["id"])
            vectors.append(np.frombuffer(row["embedding"], dtype=np.float32))

        if not ids:
            return []

        query_vec = np.frombuffer(query_embedding, dtype=np.float32)
        matrix = np.vstack(vectors)

        query_norm = np.linalg.norm(query_vec)
        matrix_norms = np.linalg.norm(matrix, axis=1)
        # Avoid division by zero for degenerate (all-zero) vectors
        denom = matrix_norms * query_norm
        denom[denom == 0] = np.inf
        similarities = (matrix @ query_vec) / denom

        ranked = np.argsort(-similarities)[:limit]
        # Drop candidates that aren't a real semantic match — cosine
        # similarity is never exactly zero, so without this cutoff every
        # query would "match" every entry in the KB.
        ranked = [i for i in ranked if similarities[i] >= MIN_COSINE_SIMILARITY]
        return [ids[i] for i in ranked]

    def find_similar_by_embedding(
        self,
        embedding: bytes,
        exclude_id: str,
        limit: int = SUGGESTION_TOP_K,
        min_similarity: float = SUGGESTION_MIN_SIMILARITY,
    ) -> list[dict[str, Any]]:
        """
        Rank stored entries by cosine similarity to a precomputed embedding.

        Used for entity-resolution link suggestions on `remember` — the
        caller already has the new/updated entry's embedding from indexing,
        so this skips re-embedding a query string (unlike `_vector_search`).

        Args:
            embedding: Raw float32 embedding bytes to compare against.
            exclude_id: Entry id to exclude (the entry itself).
            limit: Maximum number of suggestions to return.
            min_similarity: Cosine similarity floor — stricter than
                MIN_COSINE_SIMILARITY, since a suggested link should be
                worth cross-referencing, not merely worth surfacing in
                search results.

        Returns:
            List of dicts with id and score, most similar first. Empty
            when no other entry clears the similarity floor.
        """

        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT id, embedding FROM entries "
                "WHERE embedding IS NOT NULL AND id != ?",
                (exclude_id,),
            )
            ids: list[str] = []
            vectors: list[np.ndarray] = []
            for row in cursor:
                ids.append(row["id"])
                vectors.append(np.frombuffer(row["embedding"], dtype=np.float32))
        finally:
            conn.close()

        if not ids:
            # No other embedded entries to compare against
            return []

        query_vec = np.frombuffer(embedding, dtype=np.float32)
        matrix = np.vstack(vectors)

        query_norm = np.linalg.norm(query_vec)
        matrix_norms = np.linalg.norm(matrix, axis=1)
        # Avoid division by zero for degenerate (all-zero) vectors
        denom = matrix_norms * query_norm
        denom[denom == 0] = np.inf
        similarities = (matrix @ query_vec) / denom

        ranked = np.argsort(-similarities)[: max(limit * 5, limit)]
        return [
            {"id": ids[i], "score": round(float(similarities[i]), 4)}
            for i in ranked
            if similarities[i] >= min_similarity
        ][:limit]

    def record_access(self, entry_ids: list[str]) -> None:
        """
        Bump usage counters for entries that were just read.

        Called only on recall (deliberate reads) — the raw signal behind
        usage-based staleness scoring. Search hits deliberately do NOT
        count: boosting whatever already ranks high creates a
        rich-get-richer feedback loop (measured on the production KB:
        MRR 0.35 with hit-inflated counters vs 0.85 without).

        Args:
            entry_ids: Ids of the entries accessed.
        """

        if not entry_ids:
            return

        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.executemany(
                "UPDATE entries SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                [(now, entry_id) for entry_id in entry_ids],
            )
            conn.commit()
        finally:
            conn.close()

    def get_usage_snapshot(self) -> dict[str, dict[str, Any]]:
        """
        Read access_count/last_accessed for every entry in the index.

        access_count/last_accessed are index-only data (see CLAUDE.md) —
        this is how a caller with only the Markdown source of truth (e.g.
        doctor's cleanup-candidate check) reaches them.

        Returns:
            Dict of entry id -> {access_count, last_accessed}.
        """

        conn = self._connect()
        try:
            cursor = conn.execute("SELECT id, access_count, last_accessed FROM entries")
            return {
                row["id"]: {
                    "access_count": row["access_count"],
                    "last_accessed": row["last_accessed"],
                }
                for row in cursor
            }
        finally:
            conn.close()

    def _apply_staleness(
        self, conn: sqlite3.Connection, fused: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Annotate fused RRF results with usage metadata — display-only,
        never folded into ranking.

        staleness = (1 + ln(1 + access_count)) × 0.5^(days_since_last_access
        / STALENESS_HALF_LIFE_DAYS), floored at 1.0 once an entry has ever
        been accessed (never-accessed entries get 1.0 too). This used to be
        multiplied into `item["score"]` and re-sorted; an eval A/B (28
        golden queries, eval/) showed that folding it into ranking is
        monotonically harmful — any nonzero frequency boost lowered MRR
        (0.857 unboosted vs 0.702 at the old formula), hitting cross-lingual
        queries hardest (RU MRR 0.750 -> 0.375). The floor also makes decay
        provably unable to fire below 1.0 whenever access_count > 0, so
        `staleness` is really just a frequency+recency indicator now, kept
        for the dashboard and doctor's cleanup-candidate check — not a
        relevance signal. Types in `no_boost_types` keep it pinned at 1.0:
        hubs are read every session, so their access count is a systematic
        skew rather than a usage signal.

        Args:
            conn: An already-open sqlite3.Connection instance.
            fused: RRF results (dicts with id and score), best-first.

        Returns:
            The same list, in the same order — annotated with
            access_count, last_accessed, and staleness.
        """

        if not fused:
            return fused

        placeholders = ",".join("?" * len(fused))
        cursor = conn.execute(
            "SELECT id, type, access_count, last_accessed FROM entries "
            f"WHERE id IN ({placeholders})",
            [item["id"] for item in fused],
        )
        usage = {row["id"]: row for row in cursor}

        now = datetime.now(timezone.utc)
        for item in fused:
            row = usage.get(item["id"])
            access_count = row["access_count"] if row else 0
            last_accessed = row["last_accessed"] if row else ""
            entry_type = row["type"] if row else ""

            decay = 1.0
            if last_accessed:
                try:
                    accessed_at = datetime.fromisoformat(last_accessed)
                    if accessed_at.tzinfo is None:
                        # record_access always writes UTC, but a value
                        # edited into the index by hand may be naive —
                        # subtracting it from an aware `now` would raise
                        accessed_at = accessed_at.replace(tzinfo=timezone.utc)
                    days = max((now - accessed_at).total_seconds(), 0.0) / 86400
                    decay = 0.5 ** (days / STALENESS_HALF_LIFE_DAYS)
                except (ValueError, TypeError):
                    logger.warning(
                        "Unusable last_accessed for %s: %s",
                        item["id"],
                        last_accessed,
                    )

            boost = (
                1.0
                if entry_type in self._no_boost_types
                else 1.0 + math.log1p(access_count)
            )
            factor = boost * decay
            if access_count > 0:
                # Floor at 1.0 so a once-read entry's displayed staleness
                # never implies "worse than never-read" purely because
                # decay ate through the frequency boost over time.
                factor = max(1.0, factor)
            item["access_count"] = access_count
            item["last_accessed"] = last_accessed
            item["staleness"] = round(factor, 4)

        # Annotated only — order and score are untouched
        return fused

    @staticmethod
    def _rrf_fuse(
        ranked_lists: list[list[str]], limit: int, k: int = RRF_K
    ) -> list[dict[str, Any]]:
        """
        Combine multiple ranked id lists via Reciprocal Rank Fusion.

        Args:
            ranked_lists: Each a list of entry ids, best match first.
            limit: Maximum number of results to return.
            k: RRF constant (standard value: 60).

        Returns:
            List of dicts with id and score (summed reciprocal ranks),
            sorted best-first.
        """

        scores: dict[str, float] = {}
        for ranked in ranked_lists:
            for rank, entry_id in enumerate(ranked):
                scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank + 1)

        fused = sorted(scores.items(), key=lambda item: -item[1])[:limit]
        return [{"id": entry_id, "score": round(score, 5)} for entry_id, score in fused]

    def search(
        self,
        query_str: str,
        tags: list[str] | None,
        limit: int,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search: BM25 keyword match fused with semantic similarity
        and literal title/tag overlap.

        Runs an FTS5 BM25 query, a cosine-similarity query over local
        Model2Vec embeddings, and a literal-substring query-token match
        against title/tags, then combines the three ranked lists with
        Reciprocal Rank Fusion. Falls back to keyword-only results if
        the embedding model is unavailable.

        Args:
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of results.
            entry_type: Optional entry type to filter by (exact match).
            part_of: Optional hub UUIDs to filter by (AND logic).

        Returns:
            List of dicts with id and score keys.
        """

        if not query_str or not query_str.strip():
            return []

        conn = self._connect()
        try:
            # Fetch deeper candidate lists than `limit` so fusion has
            # enough signal from each side before truncating to the final
            # limit — but scale the depth down on small KBs, where a flat
            # 50 would be a large fraction of the whole corpus and let a
            # staleness boost reorder queries it has no real relevance to.
            total_entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            candidate_limit = min(
                max(limit * 5, 50),
                max(limit, total_entries // CANDIDATE_FRACTION_DIVISOR),
            )

            bm25_ids = self._bm25_search(
                conn,
                query_str,
                tags,
                candidate_limit,
                entry_type=entry_type,
                part_of=part_of,
            )
            vector_ids = self._vector_search(
                conn,
                query_str,
                tags,
                candidate_limit,
                entry_type=entry_type,
                part_of=part_of,
            )
            exact_ids = self._exact_match_search(
                conn,
                query_str,
                tags,
                candidate_limit,
                entry_type=entry_type,
                part_of=part_of,
            )
            # Fuse over the full candidate pool, annotate with usage
            # metadata, then truncate — usage no longer reorders results
            # (see _apply_staleness).
            # NOTE: a third RRF list of 1-hop graph neighbors of the top
            # fused hits was tried and rejected 2026-07-25 — on the real
            # KB it never beat plain BM25+vector at any weight and tanked
            # MRR 0.85 -> 0.46 at full weight, because densely-linked hub
            # entries are neighbors of nearly every seed. The exact-match
            # channel below is a different kind of signal (literal, not
            # graph-derived) and is fused rather than gating results, so
            # it can only add candidates BM25/vector missed, not remove any.
            fused = self._rrf_fuse(
                [bm25_ids, vector_ids, exact_ids], candidate_limit
            )
            results = self._apply_staleness(conn, fused)[:limit]
        finally:
            conn.close()

        logger.info("Search '%s' returned %d results", query_str, len(results))
        return results

    def rebuild(self, entries: list[dict[str, Any]]) -> int:
        """
        Rebuild index from a list of entries.

        Drops all data and recreates from scratch in a single transaction.

        Args:
            entries: List of dicts with id, title, tags, content.

        Returns:
            Number of entries indexed.
        """

        logger.info("Rebuilding SQLite index (%d entries)", len(entries))

        conn = self._connect()
        try:
            # Capture state that must survive the DROP: content-addressed
            # embedding cache (skip re-encoding unchanged entries) and
            # usage counters (staleness signal is index-only data).
            embedding_cache: dict[str, bytes] = {}
            usage_cache: dict[str, tuple[int, str]] = {}
            try:
                cursor = conn.execute(
                    "SELECT id, content_hash, embedding, access_count, "
                    "last_accessed FROM entries"
                )
                for row in cursor:
                    if row["embedding"] is not None and row["content_hash"]:
                        embedding_cache[row["content_hash"]] = row["embedding"]
                    if row["access_count"] or row["last_accessed"]:
                        usage_cache[row["id"]] = (
                            row["access_count"],
                            row["last_accessed"],
                        )
            except sqlite3.OperationalError:
                # Legacy index without the newer columns — nothing to carry
                pass

            # Drop all tables and recreate (contentless FTS5 forbids bulk DELETE)
            conn.execute("DROP TABLE IF EXISTS relations")
            conn.execute("DROP TABLE IF EXISTS entries_fts")
            conn.execute("DROP TABLE IF EXISTS entries")
            conn.execute(_SQL_CREATE_ENTRIES)
            conn.execute(_SQL_CREATE_FTS)
            conn.execute(_SQL_CREATE_RELATIONS)
            # DROP TABLE took the index with it — recreate alongside
            conn.execute(_SQL_CREATE_RELATIONS_TARGET_INDEX)

            # Reuse cached embeddings for unchanged entries; batch-encode
            # only the rest in one model call.
            embeddings: list[bytes | None] = [
                embedding_cache.get(self._content_hash(e["title"], e["content"]))
                for e in entries
            ]
            missing = [i for i, emb in enumerate(embeddings) if emb is None]
            model = _get_model(self._embedding_model)
            if model is not None and missing:
                texts = [
                    f"{entries[i]['title']}\n{entries[i]['content']}"
                    for i in missing
                ]
                vectors = model.encode(texts).astype(np.float32)
                for i, vector in zip(missing, vectors):
                    embeddings[i] = vector.tobytes()
            if entries:
                logger.info(
                    "Rebuild embeddings: %d cached, %d encoded",
                    len(entries) - len(missing),
                    len(missing),
                )

            # Bulk insert all entries
            count = 0
            for entry, embedding in zip(entries, embeddings):
                self._index_entry_with_conn(entry, conn, embedding=embedding)
                count += 1

            # Restore usage counters for entries that still exist
            conn.executemany(
                "UPDATE entries SET access_count = ?, last_accessed = ? "
                "WHERE id = ?",
                [
                    (access_count, last_accessed, entry_id)
                    for entry_id, (access_count, last_accessed)
                    in usage_cache.items()
                ],
            )

            conn.commit()
        finally:
            conn.close()

        logger.info("Rebuild complete: %d entries indexed", count)
        return count

    def _direction_rows(
        self,
        conn: sqlite3.Connection,
        entry_id: str,
        direction: str,
        limit: int | None,
    ) -> list[dict[str, str]]:
        """
        Fetch one direction's immediate relations, newest-first.

        Args:
            direction: 'out' (entry_id is source) or 'in' (entry_id is
                target, self-links excluded).
            limit: Optional cap. Rows are ordered newest-first (rowid
                DESC — insertion order is the only recency signal the
                relations table has), so a capped result keeps the most
                recently indexed links.

        Returns:
            List of dicts with 'type', 'id', 'edge'.
        """

        limit_sql = " LIMIT ?" if limit is not None else ""
        if direction == "out":
            params: list[Any] = [entry_id]
            if limit is not None:
                params.append(limit)
            cursor = conn.execute(
                "SELECT target_id AS neighbor_id, type, edge_type FROM relations "
                "WHERE source_id = ? "
                f"ORDER BY rowid DESC{limit_sql}",
                params,
            )
        else:
            params = [entry_id, entry_id]
            if limit is not None:
                params.append(limit)
            cursor = conn.execute(
                "SELECT source_id AS neighbor_id, type, edge_type FROM relations "
                "WHERE target_id = ? AND source_id != ? "
                f"ORDER BY rowid DESC{limit_sql}",
                params,
            )
        return [
            {"type": row["type"], "id": row["neighbor_id"], "edge": row["edge_type"]}
            for row in cursor
        ]

    def _expand_hop2(
        self,
        conn: sqlite3.Connection,
        entry_id: str,
        hop1_rows: list[dict[str, str]],
        direction: str,
        limit: int | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """
        Expand one direction's hop-1 rows by one more level, same direction.

        BFS with a single visited set: a neighbor already seen (the root,
        or an earlier hop-1/hop-2 node) is never re-added — its discoverer
        gets recorded in 'via' instead. 'via' collects every hop-1 parent
        that reaches a given hop-2 node, not just the first.

        Returns:
            (rows, truncated) — rows is hop1_rows with 'hops': 1 tagged,
            plus new hop-2 rows appended ('hops': 2, 'via': [...]); 'via'
            is omitted from hop-1 rows. truncated is True if any hop-1
            node's own neighbor query hit `limit` (more may exist unseen).
        """

        visited: set[str] = {entry_id, *(row["id"] for row in hop1_rows)}
        rows: list[dict[str, Any]] = [{**row, "hops": 1} for row in hop1_rows]
        hop2_index: dict[str, dict[str, Any]] = {}
        truncated = False

        for parent in hop1_rows:
            neighbors = self._direction_rows(conn, parent["id"], direction, limit)
            if limit is not None and len(neighbors) >= limit:
                truncated = True
            for neighbor in neighbors:
                neighbor_id = neighbor["id"]
                if neighbor_id in visited:
                    continue
                existing = hop2_index.get(neighbor_id)
                if existing is not None:
                    existing["via"].append(parent["id"])
                    continue
                new_row = {**neighbor, "hops": 2, "via": [parent["id"]]}
                hop2_index[neighbor_id] = new_row
                rows.append(new_row)

        for neighbor_id in hop2_index:
            visited.add(neighbor_id)

        return rows, truncated

    def get_relations(
        self, entry_id: str, limit: int | None = None, hops: int = 1
    ) -> dict[str, Any]:
        """
        Get outgoing and incoming graph relations for an entry.

        Args:
            entry_id: UUID of the entry.
            limit: Optional cap applied to each direction independently,
                and (when hops=2) to each hop-1 node's own neighbor query.
            hops: 1 (default) returns direct relations only. 2 additionally
                walks one more level in the same direction (out-of-out,
                in-of-in — directions are never mixed). Clamped to [1, 2].

        Returns:
            Dict with 'out' and 'in' lists (each item has 'type', 'id',
            'edge', 'hops'; hop-2 items also carry 'via', the list of
            hop-1 ids that reach them) plus 'out_total'/'in_total' — the
            uncapped hop-1 counts. At hops=2, also includes 'hop2_total'
            (unique hop-2 nodes found) and 'hop2_truncated' (True if any
            hop-1 node's neighbor query was itself capped by `limit`).
        """

        hops = max(1, min(hops, 2))

        conn = self._connect()
        try:
            out_total = conn.execute(
                "SELECT COUNT(*) FROM relations WHERE source_id = ?",
                (entry_id,),
            ).fetchone()[0]
            in_total = conn.execute(
                "SELECT COUNT(*) FROM relations "
                "WHERE target_id = ? AND source_id != ?",
                (entry_id, entry_id),
            ).fetchone()[0]

            out_rows = self._direction_rows(conn, entry_id, "out", limit)
            in_rows = self._direction_rows(conn, entry_id, "in", limit)

            result: dict[str, Any] = {
                "out_total": out_total,
                "in_total": in_total,
            }
            if hops == 2:
                out, out_truncated = self._expand_hop2(
                    conn, entry_id, out_rows, "out", limit
                )
                incoming, in_truncated = self._expand_hop2(
                    conn, entry_id, in_rows, "in", limit
                )
                result["hop2_total"] = sum(
                    1 for row in (*out, *incoming) if row["hops"] == 2
                )
                result["hop2_truncated"] = out_truncated or in_truncated
            else:
                out = [{**row, "hops": 1} for row in out_rows]
                incoming = [{**row, "hops": 1} for row in in_rows]

        finally:
            conn.close()

        result["out"] = out
        result["in"] = incoming
        return result

    def get_all_relations(self) -> list[dict[str, str]]:
        """
        Get every graph relation in the index, for building a full graph.

        Returns:
            List of dicts with 'source_id', 'target_id', 'type'.
        """

        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT source_id, target_id, type, edge_type FROM relations"
            )
            relations = [
                {
                    "source_id": row["source_id"],
                    "target_id": row["target_id"],
                    "type": row["type"],
                    "edge": row["edge_type"],
                }
                for row in cursor
            ]
        finally:
            conn.close()

        return relations

    # -------------------------------------------------------------------
    # Usage analytics (query_log)
    # -------------------------------------------------------------------

    def log_query_event(
        self,
        ts: str,
        session_id: str,
        tool: str,
        query_text: str | None = None,
        entry_type: str | None = None,
        returned_ids: list[str] | None = None,
        top_result_id: str | None = None,
        entry_id: str | None = None,
        hit: bool | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """
        Append one search/recall/remember call to the query_log table.

        Args:
            ts: ISO8601 UTC timestamp of the call.
            session_id: MCP session identifier (fastmcp Context.session_id).
            tool: "search", "recall", or "remember".
            query_text: Search query string, truncated to
                QUERY_LOG_TEXT_TRUNCATE chars.
            entry_type: Entry-type filter used (search) or the entry's own
                type (remember) — powers the per-type hit distribution.
            returned_ids: Ordered result ids (search only) — needed to
                compute average recall rank, not just top_result_id.
            top_result_id: First result id (search only).
            entry_id: Target entry id (recall/remember).
            hit: Whether the call found something (search: len(results) > 0;
                recall/remember: found/succeeded).
            latency_ms: Wall-clock duration of the underlying kb call.
        """

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO query_log (ts, session_id, tool, query_text, "
                "entry_type, returned_ids, top_result_id, entry_id, hit, "
                "latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    session_id,
                    tool,
                    query_text[:QUERY_LOG_TEXT_TRUNCATE] if query_text else None,
                    entry_type,
                    json.dumps(returned_ids, ensure_ascii=False)
                    if returned_ids is not None
                    else None,
                    top_result_id,
                    entry_id,
                    None if hit is None else int(hit),
                    latency_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _session_analytics(
        self, conn: sqlite3.Connection, click_through_window_minutes: float
    ) -> dict[str, Any]:
        """
        Walk query_log's search/recall rows session-by-session to compute
        the metrics that need call order, not just aggregate counts:
        click-through, searches-per-recall, and average recall rank.

        For each recall, "the previous searches" means every search in the
        same session since the last recall (or session start) — matching
        the metric definitions in IDEA.md. Click-through and recall rank
        both look at that window's most recent search that actually
        returned the recalled entry_id.

        Args:
            conn: An already-open sqlite3.Connection instance.
            click_through_window_minutes: Max gap between a search and the
                recall of its top result for it to count as "clicked".

        Returns:
            Dict with click_through_rate, searches_per_recall (avg),
            average_recall_rank — each None when there is no data to
            compute it from.
        """

        cursor = conn.execute(
            "SELECT session_id, ts, tool, returned_ids, top_result_id, "
            "entry_id FROM query_log WHERE tool IN ('search', 'recall') "
            "ORDER BY session_id, ts"
        )

        clicked = 0
        recalls_with_prior_search = 0
        searches_per_recall_samples: list[int] = []
        recall_ranks: list[int] = []

        current_session: str | None = None
        pending_searches: list[dict[str, Any]] = []
        searches_since_recall = 0

        for row in cursor:
            if row["session_id"] != current_session:
                current_session = row["session_id"]
                pending_searches = []
                searches_since_recall = 0

            if row["tool"] == "search":
                pending_searches.append(row)
                searches_since_recall += 1
                continue

            # tool == "recall"
            if not pending_searches:
                continue

            recalls_with_prior_search += 1
            searches_per_recall_samples.append(searches_since_recall)

            recalled_id = row["entry_id"]
            for search_row in reversed(pending_searches):
                returned = (
                    json.loads(search_row["returned_ids"])
                    if search_row["returned_ids"]
                    else []
                )
                if recalled_id in returned:
                    recall_ranks.append(returned.index(recalled_id) + 1)
                    if search_row["top_result_id"] == recalled_id:
                        try:
                            search_ts = datetime.fromisoformat(search_row["ts"])
                            recall_ts = datetime.fromisoformat(row["ts"])
                            gap_minutes = (
                                recall_ts - search_ts
                            ).total_seconds() / 60
                            if 0 <= gap_minutes <= click_through_window_minutes:
                                clicked += 1
                        except (ValueError, TypeError):
                            pass
                    break

            pending_searches = []
            searches_since_recall = 0

        return {
            "click_through_rate": round(clicked / recalls_with_prior_search, 4)
            if recalls_with_prior_search
            else None,
            "searches_per_recall": round(
                sum(searches_per_recall_samples) / len(searches_per_recall_samples), 2
            )
            if searches_per_recall_samples
            else None,
            "average_recall_rank": round(sum(recall_ranks) / len(recall_ranks), 2)
            if recall_ranks
            else None,
        }

    def get_analytics_snapshot(
        self,
        click_through_window_minutes: float = CLICK_THROUGH_WINDOW_MINUTES,
        dead_entry_stale_days: float = DEAD_ENTRY_STALE_DAYS,
        zero_hit_limit: int = ZERO_HIT_QUERIES_LIMIT,
    ) -> dict[str, Any]:
        """
        Aggregate query_log + entries into the /api/analytics metric set.

        Args:
            click_through_window_minutes: See _session_analytics.
            dead_entry_stale_days: An entry with a nonzero access_count is
                "dead" once last_accessed is older than this.
            zero_hit_limit: Cap on the zero-hit-queries list, most
                frequent first.

        Returns:
            Dict covering every metric from IDEA.md's "What the team gets
            to see" table except session-cost×memory-usage (deferred,
            local-only in v1 — see kb://b47cdbc9).
        """

        conn = self._connect()
        try:
            tool_counts = dict(
                conn.execute(
                    "SELECT tool, COUNT(*) FROM query_log GROUP BY tool"
                ).fetchall()
            )
            searches = tool_counts.get("search", 0)
            recalls = tool_counts.get("recall", 0)
            remembers = tool_counts.get("remember", 0)
            reads = searches + recalls

            hit_row = conn.execute(
                "SELECT AVG(hit) FROM query_log WHERE tool = 'search'"
            ).fetchone()
            hit_rate = round(hit_row[0], 4) if hit_row[0] is not None else None

            zero_hit_queries = [
                {"query": row["query_text"], "count": row["cnt"]}
                for row in conn.execute(
                    "SELECT query_text, COUNT(*) AS cnt FROM query_log "
                    "WHERE tool = 'search' AND hit = 0 AND query_text IS NOT NULL "
                    "GROUP BY query_text ORDER BY cnt DESC LIMIT ?",
                    (zero_hit_limit,),
                )
            ]

            hit_distribution_by_type = {
                (row["entry_type"] or "(unfiltered)"): {
                    "hit_rate": round(row["avg_hit"], 4),
                    "total": row["total"],
                }
                for row in conn.execute(
                    "SELECT entry_type, AVG(hit) AS avg_hit, COUNT(*) AS total "
                    "FROM query_log WHERE tool = 'search' GROUP BY entry_type"
                )
            }

            dead_row = conn.execute(
                "SELECT COUNT(*), COUNT(CASE WHEN access_count = 0 THEN 1 END) "
                "FROM entries"
            ).fetchone()
            total_entries, never_accessed = dead_row[0], dead_row[1]
            stale_cutoff = (
                datetime.now(timezone.utc) - timedelta(days=dead_entry_stale_days)
            ).isoformat()
            stale_accessed = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE access_count > 0 "
                "AND last_accessed != '' AND last_accessed < ?",
                (stale_cutoff,),
            ).fetchone()[0]

            sessions_touching_engram = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM query_log"
            ).fetchone()[0]

            session_metrics = self._session_analytics(
                conn, click_through_window_minutes
            )
        finally:
            conn.close()

        return {
            "read_write_ratio": round(reads / remembers, 2) if remembers else None,
            "searches": searches,
            "recalls": recalls,
            "remembers": remembers,
            "hit_rate": hit_rate,
            "zero_hit_queries": zero_hit_queries,
            "hit_distribution_by_type": hit_distribution_by_type,
            "dead_entries": {
                "never_accessed": never_accessed,
                "stale": stale_accessed,
                "total_entries": total_entries,
            },
            "sessions_touching_engram": sessions_touching_engram,
            **session_metrics,
        }
