"""
Xapian search backend for Engram.

Full-text search with French stemming, graph relation indexing,
and tag filtering. The index is a rebuildable cache on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import xapian

from backend import SearchBackend, extract_relations

logger = logging.getLogger("engram")

# ---------------------------------------------------------------------------
# Xapian prefix constants
# ---------------------------------------------------------------------------

PREFIX_ID: str = "Q"
PREFIX_TITLE: str = "XTITLE"
PREFIX_TAG: str = "XTAG"
PREFIX_TYPE: str = "XTYPE"
PREFIX_RELOUT: str = "XRELOUT:"
PREFIX_RELTGT: str = "XRELTGT:"


class XapianBackend(SearchBackend):
    """
    Xapian-based search backend with configurable stemming.

    Stores a full-text index on disk. The index is a cache — Markdown
    files are the source of truth and can be rebuilt at any time.

    Args:
        index_path: Path to the Xapian index directory.
        language: Stemmer language (default: "en"). Uses Xapian's Stem class.
    """

    def __init__(self, index_path: str | Path, language: str = "en") -> None:
        """
        Initialize the Xapian backend.

        Args:
            index_path: Path to the Xapian index directory.
            language: Stemmer language code (e.g. "en", "fr", "de").

        Errors:
            Creates the directory if missing.
        """

        self._index_path = Path(index_path)
        self._index_path.mkdir(parents=True, exist_ok=True)
        self._language = language

    # -------------------------------------------------------------------
    # Internal database access
    # -------------------------------------------------------------------

    def _get_writable_db(self) -> xapian.WritableDatabase:
        """
        Open the Xapian database for writing.

        Returns:
            A WritableDatabase instance.
        """

        # Open writable database
        return xapian.WritableDatabase(str(self._index_path), xapian.DB_CREATE_OR_OPEN)

    def _get_readable_db(self) -> xapian.Database:
        """
        Open the Xapian database for reading.

        Returns:
            A Database instance.

        Errors:
            Raises DatabaseOpeningError if the index does not exist.
        """

        # Open read-only database
        return xapian.Database(str(self._index_path))

    # -------------------------------------------------------------------
    # Internal indexing
    # -------------------------------------------------------------------

    def _index_entry_with_db(
        self, entry: dict[str, Any], db: xapian.WritableDatabase
    ) -> None:
        """
        Index or update an entry in a pre-opened Xapian database.

        Does NOT commit or close the database — the caller is responsible.

        Args:
            entry: Dict with id, title, tags, content.
            db: An already-open WritableDatabase instance.
        """

        doc = xapian.Document()

        # Term generator with French stemmer
        tg = xapian.TermGenerator()
        tg.set_stemmer(xapian.Stem(self._language))
        tg.set_database(db)
        tg.set_flags(tg.FLAG_SPELLING)
        tg.set_document(doc)

        # Index title with higher weight (wdf_inc=5)
        tg.index_text(entry["title"], 5, PREFIX_TITLE)
        tg.index_text(entry["title"], 5)

        # Index tags
        for tag in entry["tags"]:
            doc.add_boolean_term(f"{PREFIX_TAG}{tag}")
            tg.index_text(tag, 1)

        # Index type (filterable boolean term + free-text match)
        entry_type = entry.get("type", "")
        if entry_type:
            type_value = entry_type.lower().strip()
            doc.add_boolean_term(f"{PREFIX_TYPE}{type_value}")
            tg.index_text(type_value, 1)

        # Index content
        tg.increase_termpos()
        tg.index_text(entry["content"], 1)

        # Index outgoing relations from kb:// links in content
        relations = extract_relations(entry["content"])
        for rel in relations:
            doc.add_boolean_term(f"{PREFIX_RELOUT}{rel['type']}:{rel['target']}")
            doc.add_boolean_term(f"{PREFIX_RELTGT}{rel['target']}")

        # Store data for retrieval (entry id)
        doc.set_data(entry["id"])

        # Unique ID term for upsert
        id_term = f"{PREFIX_ID}{entry['id']}"
        doc.add_boolean_term(id_term)
        db.replace_document(id_term, doc)

        logger.info("Indexed entry %s", entry["id"])

    # -------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------

    def index(self, entry: dict[str, Any]) -> None:
        """
        Index or update a single entry in Xapian.

        Args:
            entry: Dict with id, title, tags, content.
        """

        db = self._get_writable_db()
        self._index_entry_with_db(entry, db)
        db.commit()
        db.close()

    def unindex(self, entry_id: str) -> None:
        """
        Remove an entry from the Xapian index.

        Args:
            entry_id: UUID of the entry.
        """

        db = self._get_writable_db()
        id_term = f"{PREFIX_ID}{entry_id}"
        db.delete_document(id_term)
        db.commit()
        db.close()
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

        try:
            db = self._get_readable_db()
        except xapian.DatabaseOpeningError:
            logger.warning("Index not found — returning empty results")
            # No index
            return []

        # Query parser with French stemmer
        qp = xapian.QueryParser()
        qp.set_stemmer(xapian.Stem(self._language))
        qp.set_stemming_strategy(qp.STEM_SOME)
        qp.set_database(db)

        # Allow prefix searches
        qp.add_prefix("title", PREFIX_TITLE)
        qp.add_prefix("tag", PREFIX_TAG)
        qp.add_prefix("type", PREFIX_TYPE)

        flags = qp.FLAG_DEFAULT | qp.FLAG_SPELLING_CORRECTION | qp.FLAG_WILDCARD
        query = qp.parse_query(query_str, flags)

        # Apply tag filter if specified
        if tags:
            tag_queries = [xapian.Query(f"{PREFIX_TAG}{t}") for t in tags]
            tag_query = xapian.Query(xapian.Query.OP_AND, tag_queries)
            query = xapian.Query(xapian.Query.OP_FILTER, query, tag_query)

        enquire = xapian.Enquire(db)
        enquire.set_query(query)

        results: list[dict[str, Any]] = []
        for match in enquire.get_mset(0, limit):
            entry_id = match.document.get_data().decode("utf-8")
            results.append({"id": entry_id, "score": match.percent})

        logger.info("Search '%s' returned %d results", query_str, len(results))
        # Search complete
        return results

    def rebuild(self, entries: list[dict[str, Any]]) -> int:
        """
        Rebuild index from a list of entries.

        Args:
            entries: List of dicts with id, title, tags, content.

        Returns:
            Number of entries indexed.
        """

        logger.info("Rebuilding Xapian index (%d entries)", len(entries))

        db = xapian.WritableDatabase(
            str(self._index_path), xapian.DB_CREATE_OR_OVERWRITE
        )

        count = 0
        for entry in entries:
            self._index_entry_with_db(entry, db)
            count += 1

        db.commit()
        db.close()

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

        try:
            db = self._get_readable_db()
        except xapian.DatabaseOpeningError:
            logger.warning("Index not found — returning empty relations")
            # No index
            return {"out": out, "in": incoming}

        # --- Outgoing relations ---
        id_term = f"{PREFIX_ID}{entry_id}"
        postlist = db.postlist(id_term)
        try:
            posting = next(postlist)
            doc = db.get_document(posting.docid)

            for term_item in doc:
                term = term_item.term.decode("utf-8")
                if term.startswith(PREFIX_RELOUT):
                    remainder = term[len(PREFIX_RELOUT) :]
                    colon_pos = remainder.find(":")
                    if colon_pos == -1:
                        continue
                    rel_type = remainder[:colon_pos]
                    target_id = remainder[colon_pos + 1 :]
                    out.append({"type": rel_type, "id": target_id})
        except StopIteration:
            pass

        # --- Incoming relations (backlinks) ---
        tgt_term = f"{PREFIX_RELTGT}{entry_id}"
        for posting in db.postlist(tgt_term):
            source_doc = db.get_document(posting.docid)
            source_id = source_doc.get_data().decode("utf-8")

            if source_id == entry_id:
                continue

            for term_item in source_doc:
                term = term_item.term.decode("utf-8")
                if not term.startswith(PREFIX_RELOUT):
                    continue
                remainder = term[len(PREFIX_RELOUT) :]
                colon_pos = remainder.find(":")
                if colon_pos == -1:
                    continue
                rel_type = remainder[:colon_pos]
                target_id = remainder[colon_pos + 1 :]

                if target_id != entry_id:
                    continue

                incoming.append({"type": rel_type, "id": source_id})

        # Relations resolved
        return {"out": out, "in": incoming}
