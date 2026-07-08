"""
Hybrid search backend for Engram — SQLite FTS5 + local semantic embeddings.

Full-text search with Porter stemming, graph relation indexing, and tag
filtering (via SQLite's built-in sqlite3 module), fused with cosine
similarity over Model2Vec static embeddings (local, no cloud dependency)
using Reciprocal Rank Fusion. The database is a rebuildable cache on disk.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from model2vec import StaticModel

logger = logging.getLogger("engram")

# Default embedding model — multilingual static embeddings (256-dim),
# picked because KB content mixes Russian and English. Overridable via
# ENGRAM_EMBEDDING_MODEL for callers that construct SQLiteBackend directly.
DEFAULT_EMBEDDING_MODEL: str = "minishlab/potion-multilingual-128M"

# Reciprocal Rank Fusion constant (standard value from Cormack et al.,
# also Elasticsearch's default).
RRF_K: int = 60

# Cosine similarity below this is treated as "no semantic match" rather
# than ranked noise — unrelated text pairs score roughly -0.05 to 0.15
# under Model2Vec, while genuinely related pairs score 0.3+.
MIN_COSINE_SIMILARITY: float = 0.2

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
        # Already loaded
        return _model_cache[model_name]

    try:
        model = StaticModel.from_pretrained(model_name)
    except Exception as exc:
        logger.warning("Embedding model '%s' unavailable: %s", model_name, exc)
        # Semantic search unavailable this call
        return None

    _model_cache[model_name] = model
    # Model loaded and cached
    return model

# Regex for extracting kb:// links with optional #type fragment
RE_KB_LINK: re.Pattern[str] = re.compile(
    r"\[[^\]]*\]\(kb://([a-f0-9-]+)(?:#([a-zA-Z0-9_-]+))?\)"
)

# ---------------------------------------------------------------------------
# Relation extraction (shared utility)
# ---------------------------------------------------------------------------


def extract_relations(content: str) -> list[dict[str, str]]:
    """
    Extract kb:// link relations from Markdown content.

    Parses links of the form [label](kb://uuid) or [label](kb://uuid#type).
    When no #type fragment is present, defaults to "related".

    Args:
        content: Markdown content body.

    Returns:
        List of dicts with 'target' (UUID) and 'type' (relation type).
    """

    relations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in RE_KB_LINK.finditer(content):
        target_id = match.group(1)
        rel_type = match.group(2) or "related"

        # Deduplicate identical target+type pairs
        key = (target_id, rel_type)
        if key in seen:
            continue
        seen.add(key)

        relations.append({"target": target_id, "type": rel_type})

    # Extracted
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
    embedding BLOB
)
"""

_SQL_CREATE_FTS: str = """
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title, content, tags,
    content='',
    tokenize='porter unicode61'
)
"""

_SQL_CREATE_RELATIONS: str = """
CREATE TABLE IF NOT EXISTS relations (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (source_id, target_id, type)
)
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
        self, db_path: str | Path, embedding_model: str | None = None
    ) -> None:
        """
        Initialize the SQLite backend and create tables if needed.

        Opens the database in WAL mode for concurrent read access.

        Args:
            db_path: Path to the SQLite database file.
            embedding_model: HuggingFace hub id of the Model2Vec model.

        Errors:
            Creates parent directories if missing.
        """

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_model = embedding_model or os.environ.get(
            "ENGRAM_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        )

        # Initialize schema
        self._ensure_schema()

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
            # Semantic search unavailable
            return None

        vector = model.encode([text])[0].astype(np.float32)
        # Embedded
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

        # Connection opened
        return conn

    def _ensure_schema(self) -> None:
        """
        Create tables and FTS virtual table if they do not exist.

        Runs all CREATE IF NOT EXISTS statements in a single transaction.
        """

        conn = self._connect()
        try:
            conn.execute(_SQL_CREATE_ENTRIES)
            conn.execute(_SQL_CREATE_FTS)
            conn.execute(_SQL_CREATE_RELATIONS)

            # Migrate pre-existing databases that predate the embedding column
            try:
                conn.execute("ALTER TABLE entries ADD COLUMN embedding BLOB")
            except sqlite3.OperationalError:
                # Column already present
                pass

            conn.commit()
        finally:
            conn.close()

        # Schema ready
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
        tags_json = json.dumps(tags, ensure_ascii=False)
        # Flatten tags to a space-separated string for FTS indexing
        tags_text = " ".join(tags)

        if embedding is None:
            embedding = self._embed_text(f"{title}\n{content}")

        # Upsert into entries table
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(id, title, tags, type, resource, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, title, tags_json, entry_type, resource, embedding),
        )

        # Delete old FTS row if it exists (contentless FTS requires manual delete)
        conn.execute(
            "DELETE FROM entries_fts WHERE rowid = ("
            "  SELECT rowid FROM entries WHERE id = ?"
            ")",
            (entry_id,),
        )

        # Insert into FTS using the rowid from the entries table
        conn.execute(
            "INSERT INTO entries_fts (rowid, title, content, tags) "
            "VALUES ((SELECT rowid FROM entries WHERE id = ?), ?, ?, ?)",
            (entry_id, title, content, tags_text),
        )

        # Extract and store relations from kb:// links
        relations = extract_relations(content)

        # Remove old relations for this source
        conn.execute("DELETE FROM relations WHERE source_id = ?", (entry_id,))

        # Insert new relations
        for rel in relations:
            conn.execute(
                "INSERT OR IGNORE INTO relations (source_id, target_id, type) "
                "VALUES (?, ?, ?)",
                (entry_id, rel["target"], rel["type"]),
            )

        # Entry indexed
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
            # Delete FTS row first (needs rowid from entries)
            conn.execute(
                "DELETE FROM entries_fts WHERE rowid = ("
                "  SELECT rowid FROM entries WHERE id = ?"
                ")",
                (entry_id,),
            )

            # Delete relations (both directions)
            conn.execute("DELETE FROM relations WHERE source_id = ?", (entry_id,))
            conn.execute("DELETE FROM relations WHERE target_id = ?", (entry_id,))

            # Delete from entries table
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

            conn.commit()
        finally:
            conn.close()

        # Unindexed
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
            # No filter
            return "", []

        conditions = []
        params: list[str] = []
        for tag in tags:
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}.tags) WHERE value = ?)"
            )
            params.append(tag)

        # Filter built
        return "AND " + " AND ".join(conditions), params

    def _bm25_search(
        self,
        conn: sqlite3.Connection,
        query_str: str,
        tags: list[str] | None,
        limit: int,
    ) -> list[str]:
        """
        Rank entries by BM25 keyword match.

        Args:
            conn: An already-open sqlite3.Connection instance.
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of ids to return.

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
        fts_query = " ".join(f'"{token}"' for token in tokens)
        tag_where, tag_params = self._tag_filter_clause(tags)

        sql = (
            "SELECT e.id, bm25(entries_fts) AS score "
            "FROM entries_fts AS f "
            "JOIN entries AS e ON e.rowid = f.rowid "
            f"WHERE entries_fts MATCH ? {tag_where} "
            "ORDER BY score "
            "LIMIT ?"
        )

        try:
            cursor = conn.execute(sql, [fts_query, *tag_params, limit])
            # BM25 ranked ids
            return [row["id"] for row in cursor]
        except sqlite3.OperationalError as exc:
            logger.warning("BM25 search error for '%s': %s", query_str, exc)
            # Degrade to no keyword signal
            return []

    def _vector_search(
        self,
        conn: sqlite3.Connection,
        query_str: str,
        tags: list[str] | None,
        limit: int,
    ) -> list[str]:
        """
        Rank entries by cosine similarity of their stored embeddings.

        Args:
            conn: An already-open sqlite3.Connection instance.
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of ids to return.

        Returns:
            List of entry ids, most similar first. Empty when the
            embedding model is unavailable or no entries have embeddings.
        """

        query_embedding = self._embed_text(query_str)
        if query_embedding is None:
            # Semantic search unavailable this call
            return []

        tag_where, tag_params = self._tag_filter_clause(tags)
        sql = (
            "SELECT id, embedding FROM entries e "
            f"WHERE embedding IS NOT NULL {tag_where}"
        )
        cursor = conn.execute(sql, tag_params)

        ids: list[str] = []
        vectors: list[np.ndarray] = []
        for row in cursor:
            ids.append(row["id"])
            vectors.append(np.frombuffer(row["embedding"], dtype=np.float32))

        if not ids:
            # No embedded entries
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
        # Vector ranked ids
        return [ids[i] for i in ranked]

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
        # Fused ranking
        return [{"id": entry_id, "score": round(score, 5)} for entry_id, score in fused]

    def search(
        self, query_str: str, tags: list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        """
        Hybrid search: BM25 keyword match fused with semantic similarity.

        Runs both an FTS5 BM25 query and a cosine-similarity query over
        local Model2Vec embeddings, then combines the two ranked lists
        with Reciprocal Rank Fusion. Falls back to keyword-only results
        if the embedding model is unavailable.

        Args:
            query_str: Search query string.
            tags: Optional list of normalized tags to filter by (AND logic).
            limit: Maximum number of results.

        Returns:
            List of dicts with id and score keys.
        """

        if not query_str or not query_str.strip():
            # Empty query
            return []

        # Fetch deeper candidate lists than `limit` so fusion has enough
        # signal from each side before truncating to the final limit.
        candidate_limit = max(limit * 5, 50)

        conn = self._connect()
        try:
            bm25_ids = self._bm25_search(conn, query_str, tags, candidate_limit)
            vector_ids = self._vector_search(conn, query_str, tags, candidate_limit)
        finally:
            conn.close()

        results = self._rrf_fuse([bm25_ids, vector_ids], limit)

        logger.info("Search '%s' returned %d results", query_str, len(results))
        # Search complete
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
            # Drop all tables and recreate (contentless FTS5 forbids bulk DELETE)
            conn.execute("DROP TABLE IF EXISTS relations")
            conn.execute("DROP TABLE IF EXISTS entries_fts")
            conn.execute("DROP TABLE IF EXISTS entries")
            conn.execute(_SQL_CREATE_ENTRIES)
            conn.execute(_SQL_CREATE_FTS)
            conn.execute(_SQL_CREATE_RELATIONS)

            # Batch-encode all entry texts in one model call (much faster
            # than one encode() per entry).
            embeddings: list[bytes | None] = [None] * len(entries)
            model = _get_model(self._embedding_model)
            if model is not None and entries:
                texts = [f"{e['title']}\n{e['content']}" for e in entries]
                vectors = model.encode(texts).astype(np.float32)
                embeddings = [v.tobytes() for v in vectors]

            # Bulk insert all entries
            count = 0
            for entry, embedding in zip(entries, embeddings):
                self._index_entry_with_conn(entry, conn, embedding=embedding)
                count += 1

            conn.commit()
        finally:
            conn.close()

        logger.info("Rebuild complete: %d entries indexed", count)
        # Rebuild done
        return count

    def get_relations(self, entry_id: str) -> dict[str, list[dict[str, str]]]:
        """
        Get outgoing and incoming graph relations for an entry.

        Args:
            entry_id: UUID of the entry.

        Returns:
            Dict with 'out' and 'in' lists. Each item has 'type' and 'id'.
        """

        out: list[dict[str, str]] = []
        incoming: list[dict[str, str]] = []

        conn = self._connect()
        try:
            # Outgoing relations (this entry links to others)
            cursor = conn.execute(
                "SELECT target_id, type FROM relations WHERE source_id = ?",
                (entry_id,),
            )
            for row in cursor:
                out.append({"type": row["type"], "id": row["target_id"]})

            # Incoming relations (other entries link to this one)
            cursor = conn.execute(
                "SELECT source_id, type FROM relations "
                "WHERE target_id = ? AND source_id != ?",
                (entry_id, entry_id),
            )
            for row in cursor:
                incoming.append({"type": row["type"], "id": row["source_id"]})

        finally:
            conn.close()

        # Relations resolved
        return {"out": out, "in": incoming}
