"""
SQLite FTS5 search backend for Engram.

Full-text search with Porter stemming, graph relation indexing,
and tag filtering. Uses Python's built-in sqlite3 module — zero
external dependencies. The database is a rebuildable cache on disk.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend import SearchBackend, extract_relations

logger = logging.getLogger("engram")

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_SQL_CREATE_ENTRIES: str = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    tags TEXT NOT NULL,  -- JSON array
    type TEXT DEFAULT '',
    resource TEXT DEFAULT ''
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


class SQLiteBackend(SearchBackend):
    """
    SQLite FTS5 search backend with Porter stemming.

    Stores a full-text index in a single SQLite database file. The database
    is a cache — Markdown files are the source of truth and can be rebuilt
    at any time.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str | Path) -> None:
        """
        Initialize the SQLite backend and create tables if needed.

        Opens the database in WAL mode for concurrent read access.

        Args:
            db_path: Path to the SQLite database file.

        Errors:
            Creates parent directories if missing.
        """

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._ensure_schema()

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
            conn.commit()
        finally:
            conn.close()

        # Schema ready
        logger.info("SQLite schema ensured at %s", self._db_path)

    # -------------------------------------------------------------------
    # Internal indexing helpers
    # -------------------------------------------------------------------

    def _index_entry_with_conn(
        self, entry: dict[str, Any], conn: sqlite3.Connection
    ) -> None:
        """
        Index or update an entry in a pre-opened connection.

        Does NOT commit — the caller is responsible for committing.

        Args:
            entry: Dict with id, title, tags, content.
            conn: An already-open sqlite3.Connection instance.
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

        # Upsert into entries table
        conn.execute(
            "INSERT OR REPLACE INTO entries (id, title, tags, type, resource) "
            "VALUES (?, ?, ?, ?, ?)",
            (entry_id, title, tags_json, entry_type, resource),
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

    def search(
        self, query_str: str, tags: list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        """
        Full-text search with optional tag filtering.

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

        conn = self._connect()
        try:
            # Build FTS5 query — escape double quotes in user input
            fts_query = query_str.replace('"', '""')

            if tags:
                # Join with entries table to filter by tags
                # Build tag filter: each tag must be present in the JSON array
                tag_conditions = []
                tag_params: list[str] = []
                for tag in tags:
                    tag_conditions.append(
                        "EXISTS (  SELECT 1 FROM json_each(e.tags) WHERE value = ?)"
                    )
                    tag_params.append(tag)

                tag_where = " AND ".join(tag_conditions)

                sql = (
                    "SELECT e.id, bm25(entries_fts) AS score "
                    "FROM entries_fts AS f "
                    "JOIN entries AS e ON e.rowid = f.rowid "
                    f"WHERE entries_fts MATCH ? AND {tag_where} "
                    "ORDER BY score "
                    "LIMIT ?"
                )
                params: list[str | int] = [fts_query, *tag_params, limit]
            else:
                sql = (
                    "SELECT e.id, bm25(entries_fts) AS score "
                    "FROM entries_fts AS f "
                    "JOIN entries AS e ON e.rowid = f.rowid "
                    "WHERE entries_fts MATCH ? "
                    "ORDER BY score "
                    "LIMIT ?"
                )
                params = [fts_query, limit]

            cursor = conn.execute(sql, params)
            results: list[dict[str, Any]] = []

            for row in cursor:
                # bm25() returns negative values (lower = better match)
                # Convert to a positive score (0-100 scale approximation)
                raw_score = row["score"]
                positive_score = max(0, round(-raw_score * 10, 1))
                results.append({"id": row["id"], "score": positive_score})

        except sqlite3.OperationalError as exc:
            # FTS5 query syntax error or no table — return empty
            logger.warning("Search error for '%s': %s", query_str, exc)
            return []
        finally:
            conn.close()

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

            # Bulk insert all entries
            count = 0
            for entry in entries:
                self._index_entry_with_conn(entry, conn)
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
