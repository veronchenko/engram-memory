"""
Knowledge Base — Markdown files + SQLite FTS5 search backend.

Manages entries stored as Markdown files with YAML frontmatter. Each entry
has a UUID, title, tags list, and content body. SQLite FTS5 provides
full-text search with Porter stemming.

Source of truth: the Markdown files. The search index is a rebuildable cache.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from search_backend import SQLiteBackend, extract_relations

logger = logging.getLogger("engram")

# Default search limit
DEFAULT_SEARCH_LIMIT: int = 10
DEFAULT_LIST_LIMIT: int = 50

# Duplicate detection threshold (SequenceMatcher ratio, 0.0-1.0)
DUPLICATE_THRESHOLD: float = 0.75

# Over-fetch multiplier for search() when filtering out superseded entries,
# so trimming back down to `limit` still returns a full page when possible.
_SUPERSEDED_FETCH_MULTIPLIER: int = 3
_SUPERSEDED_FETCH_CAP: int = 300

# Regex for parsing YAML frontmatter (anchored to start of file, line-start ---)
_FRONTMATTER_RE: re.Pattern[str] = re.compile(r"\A---\n(.*?)\n---\n(.*)", re.DOTALL)

# Regex for validating UUID format (path traversal prevention)
_UUID_RE: re.Pattern[str] = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
)


def _validate_entry_id(entry_id: str) -> bool:
    """
    Validate that an entry_id is a well-formed UUID.

    Prevents path traversal attacks by rejecting any value that is not
    a strict lowercase UUID (8-4-4-4-12 hex characters).

    Args:
        entry_id: The entry identifier to validate.

    Returns:
        True if valid UUID format, False otherwise.
    """

    # Validated
    return bool(_UUID_RE.match(entry_id))


class KnowledgeBase:
    """
    Knowledge base backed by Markdown files and a pluggable search backend.

    Args:
        data_path: Root path for knowledge data (contains entries/ and index/).
        backend: Optional search backend instance. Defaults to SQLiteBackend.
    """

    def __init__(self, data_path: str, backend: SQLiteBackend | None = None) -> None:
        """
        Initialize the knowledge base.

        Args:
            data_path: Root directory for knowledge storage.
            backend: Search backend instance. When None, creates a
                SQLiteBackend at data_path/index/engram.db.

        Errors:
            Creates entries/ subdirectory if missing.
        """

        self._data_path = Path(data_path)
        self._entries_path = self._data_path / "entries"
        self._index_path = self._data_path / "index" / "engram.db"

        # Ensure entries directory exists
        self._entries_path.mkdir(parents=True, exist_ok=True)

        # Initialize search backend (default: SQLite FTS5)
        if backend is None:
            backend = SQLiteBackend(self._index_path)
        self._backend = backend

        # In-memory metadata cache: entry_id -> {title, tags}
        self._meta_cache: dict[str, dict[str, Any]] = {}
        self._load_meta_cache()

        logger.info(
            "KnowledgeBase initialized — entries: %s, backend: %s, cached: %d",
            self._entries_path,
            type(self._backend).__name__,
            len(self._meta_cache),
        )

    # -----------------------------------------------------------------------
    # Metadata cache
    # -----------------------------------------------------------------------

    def _load_meta_cache(self) -> None:
        """
        Load title and tags for all entries into memory.

        Scans all .md files once on init. Subsequent reads of metadata
        (find_similar, list_entries, list_tags, _resolve_title) use the
        cache instead of hitting disk.
        """

        self._meta_cache.clear()
        for filepath in self._entries_path.glob("*.md"):
            entry = self._read_entry(filepath)
            if entry and entry["id"]:
                self._meta_cache[entry["id"]] = {
                    "title": entry["title"],
                    "tags": entry["tags"],
                    "type": entry.get("type", ""),
                    "superseded_by": entry.get("superseded_by", ""),
                }

        # Cache loaded
        logger.info("Metadata cache loaded: %d entries", len(self._meta_cache))

    def _update_meta_cache(
        self,
        entry_id: str,
        title: str,
        tags: list[str],
        entry_type: str = "",
        superseded_by: str = "",
    ) -> None:
        """
        Update or insert a single entry in the metadata cache.

        Args:
            entry_id: UUID of the entry.
            title: Entry title.
            tags: Normalized tag list.
            entry_type: Entry classification (e.g. hub, decision, diagnostic).
            superseded_by: UUID of the entry that replaced this one, if any.
        """

        # Upsert cache entry
        self._meta_cache[entry_id] = {
            "title": title,
            "tags": tags,
            "type": entry_type,
            "superseded_by": superseded_by,
        }

    def _remove_from_meta_cache(self, entry_id: str) -> None:
        """
        Remove an entry from the metadata cache.

        Args:
            entry_id: UUID of the entry (no-op if absent).
        """

        # Remove if present
        self._meta_cache.pop(entry_id, None)

    # -----------------------------------------------------------------------
    # Markdown file operations
    # -----------------------------------------------------------------------

    def _read_entry(self, filepath: Path) -> dict[str, Any] | None:
        """
        Parse a Markdown entry file (YAML frontmatter + body).

        Args:
            filepath: Path to the .md file.

        Returns:
            Dict with id, title, tags, content keys, or None if unparseable.
        """

        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", filepath, exc)
            # Unreadable file
            return None

        # Parse frontmatter with anchored regex (immune to --- in content)
        fm_match = _FRONTMATTER_RE.match(text)
        if not fm_match:
            logger.warning("No valid frontmatter in %s", filepath)
            # Missing or malformed frontmatter
            return None

        try:
            meta = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as exc:
            logger.warning("YAML parse error in %s: %s", filepath, exc)
            # Bad YAML
            return None

        if not isinstance(meta, dict):
            logger.warning("Frontmatter is not a dict in %s", filepath)
            # Invalid structure
            return None

        content = fm_match.group(2).strip()

        # Parsed entry
        return {
            "id": str(meta.get("id", "")),
            "title": str(meta.get("title", "")),
            "tags": _normalize_tags(meta.get("tags", [])),
            "type": str(meta.get("type", "")),
            "resource": str(meta.get("resource", "")),
            "valid_at": str(meta.get("valid_at", "")),
            "superseded_by": str(meta.get("superseded_by", "")),
            "supersedes": str(meta.get("supersedes", "")),
            "content": content,
        }

    def _write_entry(self, entry: dict[str, Any]) -> Path:
        """
        Write an entry to a Markdown file with YAML frontmatter.

        Uses write-to-temp-then-rename to avoid leaving partial files on
        disk if the write fails (disk full, permission error, etc.).

        Args:
            entry: Dict with id, title, tags, content.

        Returns:
            Path to the written file.

        Errors:
            Raises OSError if the file cannot be written. Cleans up the
            temporary file on failure.
        """

        filepath = self._entries_path / f"{entry['id']}.md"
        tmp = filepath.with_suffix(".md.tmp")

        fm_dict: dict[str, Any] = {
            "id": entry["id"],
            "title": entry["title"],
            "tags": entry["tags"],
            "type": entry["type"],
        }
        # resource and bi-temporal fields are genuinely optional — omit when empty
        if entry.get("resource"):
            fm_dict["resource"] = entry["resource"]
        if entry.get("valid_at"):
            fm_dict["valid_at"] = entry["valid_at"]
        if entry.get("superseded_by"):
            fm_dict["superseded_by"] = entry["superseded_by"]
        if entry.get("supersedes"):
            fm_dict["supersedes"] = entry["supersedes"]

        frontmatter = yaml.dump(
            fm_dict,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()

        text = f"---\n{frontmatter}\n---\n\n{entry['content']}\n"

        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.rename(filepath)
        except OSError:
            # Clean up partial temp file
            tmp.unlink(missing_ok=True)
            raise

        logger.info("Wrote entry %s to %s", entry["id"], filepath)
        # File written
        return filepath

    def _delete_entry_file(self, entry_id: str) -> bool:
        """
        Delete the Markdown file for an entry.

        Args:
            entry_id: UUID of the entry.

        Returns:
            True if deleted, False if not found.
        """

        filepath = self._entries_path / f"{entry_id}.md"
        if filepath.exists():
            filepath.unlink()
            logger.info("Deleted file %s", filepath)
            # Deleted
            return True

        logger.warning("File not found for deletion: %s", filepath)
        # Not found
        return False

    # -----------------------------------------------------------------------
    # Relation extraction (static — delegates to backends module)
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_relations(content: str) -> list[dict[str, str]]:
        """
        Extract kb:// link relations from Markdown content.

        Parses links of the form [label](kb://uuid) or [label](kb://uuid#type).
        When no #type fragment is present, defaults to "related".

        Delegates to backends.extract_relations() — kept as a static method
        for backward compatibility with tests calling
        KnowledgeBase._extract_relations().

        Args:
            content: Markdown content body.

        Returns:
            List of dicts with 'target' (UUID) and 'type' (relation type).
        """

        # Delegate to shared utility
        return extract_relations(content)

    # -----------------------------------------------------------------------
    # CRUD operations (file + backend)
    # -----------------------------------------------------------------------

    def _supersede(
        self,
        old_entry: dict[str, Any],
        title: str,
        content: str,
        tags: list[str],
        entry_type: str,
        resource: str,
    ) -> dict[str, Any]:
        """
        Replace an existing entry with a new version, preserving history.

        The old entry's file is rewritten with `superseded_by` pointing at
        the new entry's id; its title/content/tags are left untouched. The
        new entry is created fresh with `supersedes` pointing back.

        Args:
            old_entry: Full dict of the entry being replaced (from self.get()).
            title: New entry's title.
            content: New entry's body.
            tags: New entry's tags.
            entry_type: New entry's classification.
            resource: New entry's resource.

        Returns:
            Dict with id, title, action='superseded', previous_id.
        """

        old_id = old_entry["id"]
        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        old_entry["superseded_by"] = new_id
        self._write_entry(old_entry)
        self._backend.index(old_entry)
        self._update_meta_cache(
            old_id,
            old_entry["title"],
            old_entry["tags"],
            old_entry.get("type", ""),
            superseded_by=new_id,
        )

        new_entry = {
            "id": new_id,
            "title": title,
            "tags": tags,
            "type": entry_type,
            "resource": resource,
            "valid_at": now,
            "supersedes": old_id,
            "content": content,
        }
        self._write_entry(new_entry)
        self._backend.index(new_entry)
        self._update_meta_cache(new_id, title, tags, entry_type)

        logger.info("Entry %s superseded by %s: %s", old_id, new_id, title)
        # Superseded
        return {
            "id": new_id,
            "title": title,
            "action": "superseded",
            "previous_id": old_id,
        }

    def remember(
        self,
        title: str,
        content: str,
        tags: list[str],
        entry_type: str,
        entry_id: str | None = None,
        force: bool = False,
        resource: str = "",
        supersede: bool = False,
    ) -> dict[str, Any]:
        """
        Upsert an entry: update if it exists, create if it doesn't.

        Resolution order:
        1. If entry_id is provided -> update that entry
        2. If no entry_id -> search for similar titles
           - If a match is found above threshold -> update the best match
           - If no match -> create a new entry
        3. If force=True -> always create new (skip duplicate detection)

        Args:
            title: Entry title.
            content: Entry body (Markdown).
            tags: List of tags.
            entry_type: Classification (e.g. hub, decision, diagnostic).
                Required — every entry must declare its type.
            entry_id: Optional UUID of an existing entry to update.
            force: Skip duplicate detection and always create new.
            resource: Optional canonical URI for the underlying asset.
            supersede: When updating an existing entry (via entry_id or
                duplicate match), create a new version instead of overwriting
                in place. The old entry is kept with `superseded_by` set to
                the new entry's id; the new entry gets `supersedes` pointing
                back and a fresh `valid_at`. No-op if there is no existing
                entry to supersede (falls through to plain creation).

        Returns:
            Dict with id, title, and action ('created', 'updated', or
            'superseded'), or an 'error' if entry_type is missing.
        """

        if not entry_type or not entry_type.strip():
            logger.warning("Remember rejected — entry_type is required")
            # Missing required field
            return {"error": "entry_type is required"}

        tags = _normalize_tags(tags)

        # Case 1: explicit ID -> update
        if entry_id:
            if not _validate_entry_id(entry_id):
                logger.warning("Invalid entry_id rejected: %s", entry_id)
                # Invalid ID format
                return {"error": f"Invalid entry_id: {entry_id}"}

            existing = self.get(entry_id)
            if not existing:
                logger.warning("Remember failed — entry %s not found", entry_id)
                # Not found
                return {"error": f"Entry {entry_id} not found"}

            if supersede:
                # New version, old entry preserved as history
                return self._supersede(
                    existing, title, content, tags, entry_type, resource
                )

            existing["title"] = title
            existing["content"] = content
            existing["tags"] = tags
            existing["type"] = entry_type
            existing["resource"] = resource
            if not existing.get("valid_at"):
                existing["valid_at"] = datetime.now(timezone.utc).isoformat()

            self._write_entry(existing)
            self._backend.index(existing)
            self._update_meta_cache(entry_id, title, tags, entry_type)

            logger.info("Updated entry %s: %s", entry_id, title)
            # Updated
            return {"id": entry_id, "title": title, "action": "updated"}

        # Case 2: no ID, check for duplicates (unless forced)
        if not force:
            similar = self.find_similar(title)
            if similar:
                # Update the best match
                best = similar[0]
                best_entry = self.get(best["id"])
                if best_entry:
                    if supersede:
                        # New version, old entry preserved as history
                        return self._supersede(
                            best_entry, title, content, tags, entry_type, resource
                        )

                    best_entry["title"] = title
                    best_entry["content"] = content
                    best_entry["tags"] = tags
                    best_entry["type"] = entry_type
                    best_entry["resource"] = resource
                    if not best_entry.get("valid_at"):
                        best_entry["valid_at"] = datetime.now(timezone.utc).isoformat()

                    self._write_entry(best_entry)
                    self._backend.index(best_entry)
                    self._update_meta_cache(best["id"], title, tags, entry_type)

                    logger.info(
                        "Updated existing entry %s (similarity %d%%): %s",
                        best["id"],
                        best["score"],
                        title,
                    )
                    # Updated via duplicate match
                    return {
                        "id": best["id"],
                        "title": title,
                        "action": "updated",
                        "matched": best["title"],
                        "similarity": best["score"],
                    }

        # Case 3: create new
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "title": title,
            "tags": tags,
            "type": entry_type,
            "resource": resource,
            "valid_at": datetime.now(timezone.utc).isoformat(),
            "content": content,
        }

        self._write_entry(entry)
        self._backend.index(entry)
        self._update_meta_cache(entry_id, title, tags, entry_type)

        logger.info("Created new entry %s: %s", entry_id, title)
        # Entry created
        return {"id": entry_id, "title": title, "action": "created"}

    def get(self, entry_id: str, with_relations: bool = False) -> dict[str, Any] | None:
        """
        Read the full content of an entry.

        Args:
            entry_id: UUID of the entry.
            with_relations: Include graph relations (outgoing and incoming links).

        Returns:
            Entry dict or None if not found. When with_relations is True, includes
            a 'relations' key with 'out' and 'in' lists.
        """

        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            # Invalid ID format
            return None

        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            # Not found
            return None

        entry = self._read_entry(filepath)
        if not entry:
            # Unparseable
            return None

        # Append graph relations if requested
        if with_relations:
            entry["relations"] = self.get_relations(entry_id)

        # Entry loaded
        return entry

    def get_relations(self, entry_id: str) -> dict[str, list[dict[str, str]]]:
        """
        Get all graph relations for an entry (outgoing and incoming).

        Delegates to the search backend for raw relation data (id + type),
        then resolves titles from the metadata cache or Markdown files.

        Args:
            entry_id: UUID of the entry.

        Returns:
            Dict with 'out' list (outgoing) and 'in' list (incoming/backlinks).
            Each item has 'type', 'id', and 'title' keys.
        """

        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            # Invalid ID format
            return {"out": [], "in": []}

        # Get raw relations from backend (id + type only)
        raw = self._backend.get_relations(entry_id)

        # Resolve titles for outgoing relations
        out: list[dict[str, str]] = []
        for rel in raw["out"]:
            title = self._resolve_title(rel["id"])
            out.append({"type": rel["type"], "id": rel["id"], "title": title})

        # Resolve titles for incoming relations
        incoming: list[dict[str, str]] = []
        for rel in raw["in"]:
            title = self._resolve_title(rel["id"])
            incoming.append({"type": rel["type"], "id": rel["id"], "title": title})

        # Relations resolved
        return {"out": out, "in": incoming}

    def _resolve_title(self, entry_id: str) -> str:
        """
        Resolve the title of an entry, using the metadata cache first.

        Falls back to reading the Markdown file if the entry is not cached.

        Args:
            entry_id: UUID of the entry.

        Returns:
            Entry title, or "(unknown)" if not found.
        """

        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            # Invalid ID format
            return "(unknown)"

        # Check cache first
        cached = self._meta_cache.get(entry_id)
        if cached:
            # Title from cache
            return cached["title"]

        # Fallback to disk (entry may not be cached yet)
        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            # Missing file
            return "(unknown)"

        entry = self._read_entry(filepath)
        if not entry:
            # Unparseable file
            return "(unknown)"

        # Title resolved from disk
        return entry["title"]

    def entry_path(self, entry_id: str) -> Path | None:
        """
        Return the filesystem path for an entry.

        Args:
            entry_id: UUID of the entry.

        Returns:
            Path to the Markdown file, or None if invalid ID.
        """

        if not _validate_entry_id(entry_id):
            # Invalid ID format
            return None

        # Entry path resolved
        return self._entries_path / f"{entry_id}.md"

    def delete(self, entry_id: str) -> bool:
        """
        Delete an entry (file + index).

        Args:
            entry_id: UUID of the entry.

        Returns:
            True if deleted, False if not found.
        """

        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            # Invalid ID format
            return False

        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            logger.warning("Delete failed — entry %s not found", entry_id)
            # Not found
            return False

        self._delete_entry_file(entry_id)
        self._remove_from_meta_cache(entry_id)

        try:
            self._backend.unindex(entry_id)
        except Exception:
            logger.warning("Entry %s not in index (already removed?)", entry_id)

        logger.info("Deleted entry %s", entry_id)
        # Deleted
        return True

    # -----------------------------------------------------------------------
    # Search operations
    # -----------------------------------------------------------------------

    def search(
        self,
        query_str: str,
        tags: list[str] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Full-text search with optional tag filtering.

        Delegates to the search backend for raw results (id + score),
        then enriches with title, tags, and snippets from Markdown files.

        Args:
            query_str: Search query string.
            tags: Optional list of tags to filter by (AND logic).
            limit: Maximum number of results.
            include_superseded: Include entries that have been superseded
                (have a non-empty superseded_by). Defaults to hiding them.

        Returns:
            List of dicts with id, title, tags, type, snippet, score.
        """

        # Normalize tags before passing to backend
        normalized_tags = _normalize_tags(tags) if tags else None

        # Over-fetch when filtering, so trimming back to `limit` still
        # returns a full page — the backend has no notion of versioning.
        fetch_limit = limit
        if not include_superseded:
            fetch_limit = min(limit * _SUPERSEDED_FETCH_MULTIPLIER, _SUPERSEDED_FETCH_CAP)

        # Get raw search results from backend (id + score)
        raw_results = self._backend.search(query_str, normalized_tags, fetch_limit)

        # Enrich results with entry data from Markdown files
        results: list[dict[str, Any]] = []
        for hit in raw_results:
            if not include_superseded:
                meta = self._meta_cache.get(hit["id"])
                if meta and meta.get("superseded_by"):
                    continue

            entry = self.get(hit["id"])
            if entry:
                # Build snippet (first 200 chars of content)
                snippet = entry["content"][:200]
                if len(entry["content"]) > 200:
                    snippet += "..."

                results.append(
                    {
                        "id": entry["id"],
                        "title": entry["title"],
                        "tags": entry["tags"],
                        "type": entry.get("type", ""),
                        "snippet": snippet,
                        "score": hit["score"],
                    }
                )

            if len(results) >= limit:
                break

        logger.info("Search '%s' returned %d results", query_str, len(results))
        # Search complete
        return results

    def rebuild(self) -> dict[str, Any]:
        """
        Rebuild the search index from all Markdown files.

        Reads all valid entry files, collects them, and passes the full
        list to the backend for a single-pass rebuild. Also runs a
        lightweight schema conformance check (non-blocking) over the
        collected entries.

        Returns:
            Dict with 'count' (entries indexed) and 'warnings' — a dict
            mapping warning kind ('missing_type', 'malformed_resource')
            to a list of affected entry ids.
        """

        logger.info("Rebuilding index from %s", self._entries_path)

        # Collect all valid entries from Markdown files
        entries: list[dict[str, Any]] = []
        for filepath in sorted(self._entries_path.glob("*.md")):
            entry = self._read_entry(filepath)
            if entry and entry["id"]:
                entries.append(entry)
            else:
                logger.warning("Skipped invalid entry: %s", filepath)

        # Delegate bulk indexing to backend
        count = self._backend.rebuild(entries)

        # Reload metadata cache to stay in sync
        self._load_meta_cache()

        # Schema conformance check (non-blocking, informational only)
        warnings: dict[str, list[str]] = {
            "missing_type": [],
            "malformed_resource": [],
        }
        for entry in entries:
            if not entry.get("type"):
                warnings["missing_type"].append(entry["id"])
            resource = entry.get("resource", "").strip()
            if resource and "://" not in resource and not resource.startswith("/"):
                warnings["malformed_resource"].append(entry["id"])

        logger.info("Rebuild complete: %d entries indexed", count)
        # Rebuild done
        return {"count": count, "warnings": warnings}

    def find_similar(self, title: str, limit: int = 5) -> list[dict[str, Any]]:
        """
        Find entries with similar titles (for duplicate detection).

        Uses SequenceMatcher on normalized titles for reliable comparison,
        independent of search backend stemming/scoring quirks. Reads from
        the in-memory metadata cache instead of scanning files on disk.

        Args:
            title: Title to check against existing entries.
            limit: Maximum number of similar entries to return.

        Returns:
            List of dicts with id, title, score for similar entries.
        """

        normalized_title = title.lower().strip()
        similar = []

        for entry_id, meta in self._meta_cache.items():
            ratio = SequenceMatcher(
                None, normalized_title, meta["title"].lower().strip()
            ).ratio()

            if ratio >= DUPLICATE_THRESHOLD:
                similar.append(
                    {
                        "id": entry_id,
                        "title": meta["title"],
                        "score": int(ratio * 100),
                    }
                )

        # Sort by score descending, limit results
        similar.sort(key=lambda x: -x["score"])

        # Similarity check complete
        return similar[:limit]

    # -----------------------------------------------------------------------
    # Browse operations
    # -----------------------------------------------------------------------

    def list_entries(
        self,
        tags: list[str] | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List entries sorted by title, with optional tag filter.

        Reads from the in-memory metadata cache instead of scanning
        files on disk.

        Args:
            tags: Optional list of tags to filter by (AND logic).
            limit: Maximum number of entries.
            include_superseded: Include entries that have been superseded
                (have a non-empty superseded_by). Defaults to hiding them.

        Returns:
            List of dicts with id, title, tags, type.
        """

        filter_tags = set(_normalize_tags(tags)) if tags else None

        entries = []
        for entry_id, meta in self._meta_cache.items():
            # Apply tag filter
            if filter_tags and not filter_tags.issubset(set(meta["tags"])):
                continue

            # Hide superseded entries by default
            if not include_superseded and meta.get("superseded_by"):
                continue

            entries.append(
                {
                    "id": entry_id,
                    "title": meta["title"],
                    "tags": meta["tags"],
                    "type": meta.get("type", ""),
                }
            )

        # Sort by title
        entries.sort(key=lambda e: e["title"].lower())

        # Listed
        return entries[:limit]

    def list_tags(self) -> list[dict[str, Any]]:
        """
        List all tags with their entry counts.

        Reads from the in-memory metadata cache instead of scanning
        files on disk.

        Returns:
            List of dicts with tag and count, sorted by count descending.
        """

        tag_counts: dict[str, int] = {}

        for meta in self._meta_cache.values():
            for tag in meta["tags"]:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        result = [{"tag": tag, "count": count} for tag, count in tag_counts.items()]
        result.sort(key=lambda x: (-x["count"], x["tag"]))

        # Tags listed
        return result

    def get_graph(self, include_superseded: bool = False) -> dict[str, list[dict[str, Any]]]:
        """
        Build the full entry graph (nodes + edges) for visualization.

        Nodes come from the metadata cache; edges come from the search
        backend's relation index, filtered to only relations between two
        included nodes.

        Args:
            include_superseded: Include entries that have been superseded.
                Defaults to hiding them, matching list_entries/search.

        Returns:
            Dict with 'nodes' (id, title, tags, type) and 'edges'
            (source_id, target_id, type).
        """

        nodes = []
        node_ids: set[str] = set()
        for entry_id, meta in self._meta_cache.items():
            if not include_superseded and meta.get("superseded_by"):
                continue
            node_ids.add(entry_id)
            nodes.append(
                {
                    "id": entry_id,
                    "title": meta["title"],
                    "tags": meta["tags"],
                    "type": meta.get("type", ""),
                }
            )

        edges = [
            rel
            for rel in self._backend.get_all_relations()
            if rel["source_id"] in node_ids and rel["target_id"] in node_ids
        ]

        # Graph built
        return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_tags(tags: Any) -> list[str]:
    """
    Normalize a list of tags: lowercase, strip whitespace, reject empty.

    Args:
        tags: Raw tags input (list or other).

    Returns:
        Cleaned list of tag strings.
    """

    if not isinstance(tags, list):
        # Not a list
        return []

    normalized = []
    for tag in tags:
        clean = str(tag).lower().strip()
        if clean:
            normalized.append(clean)

    # Normalized
    return sorted(set(normalized))
