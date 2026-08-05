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
import sqlite3
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from config import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DIGEST_SAMPLE_PER_TYPE,
    DUPLICATE_THRESHOLD,
    SUPERSEDED_FETCH_CAP,
    SUPERSEDED_FETCH_MULTIPLIER,
    WRITE_GATE_CANDIDATES,
    WRITE_GATE_MIN_SIMILARITY,
)
from doctor import run_doctor
from schema import Schema, load_schema
from search_backend import SQLiteBackend, extract_relations

logger = logging.getLogger("engram")

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

    return bool(_UUID_RE.match(entry_id))


class KnowledgeBase:
    """
    Knowledge base backed by Markdown files and a pluggable search backend.

    Args:
        data_path: Root path for knowledge data (contains entries/ and index/).
        backend: Optional search backend instance. Defaults to SQLiteBackend.
        schema: Optional entry schema. Defaults to the one resolved from
            data_path (user override, else packaged).
    """

    def __init__(
        self,
        data_path: str,
        backend: SQLiteBackend | None = None,
        schema: Schema | None = None,
    ) -> None:
        """
        Initialize the knowledge base.

        Args:
            data_path: Root directory for knowledge storage.
            backend: Search backend instance. When None, creates a
                SQLiteBackend at data_path/index/engram.db.
            schema: Entry validation contract. When None, loaded from
                data_path/schema.json, falling back to the packaged one.

        Errors:
            Creates entries/ subdirectory if missing.
        """

        self._data_path = Path(data_path)
        self._entries_path = self._data_path / "entries"
        self._index_path = self._data_path / "index" / "engram.db"
        self._schema = schema if schema is not None else load_schema(data_path)

        self._entries_path.mkdir(parents=True, exist_ok=True)

        if backend is None:
            backend = SQLiteBackend(self._index_path)
        self._backend = backend

        # Which types skip the usage boost, and which edge semantics a
        # kb:// link may carry, are schema decisions — applied here rather
        # than by every caller that builds a backend by hand, since one of
        # them forgetting is a silent ranking or graph change.
        self._backend.no_boost_types = self._schema.types_where("usage_boost", False)
        self._backend.allowed_edges = self._schema.edge_types

        # In-memory metadata cache: entry_id -> {title, tags}
        self._meta_cache: dict[str, dict[str, Any]] = {}
        self._load_meta_cache()

        logger.info(
            "KnowledgeBase initialized — entries: %s, backend: %s, cached: %d",
            self._entries_path,
            type(self._backend).__name__,
            len(self._meta_cache),
        )

    @property
    def schema(self) -> Schema:
        """The entry validation contract this knowledge base was built with."""

        # Read-only access for callers that validate against the same rules
        return self._schema

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
                    "part_of": entry.get("part_of", []),
                    "superseded_by": entry.get("superseded_by", ""),
                }

        logger.info("Metadata cache loaded: %d entries", len(self._meta_cache))

    def _update_meta_cache(
        self,
        entry_id: str,
        title: str,
        tags: list[str],
        entry_type: str = "",
        part_of: list[str] | None = None,
        superseded_by: str = "",
    ) -> None:
        """
        Update or insert a single entry in the metadata cache.

        Args:
            entry_id: UUID of the entry.
            title: Entry title.
            tags: Normalized tag list.
            entry_type: Entry classification (e.g. hub, decision, diagnostic).
            part_of: UUIDs of the hubs this entry belongs to.
            superseded_by: UUID of the entry that replaced this one, if any.
        """

        self._meta_cache[entry_id] = {
            "title": title,
            "tags": tags,
            "type": entry_type,
            "part_of": part_of or [],
            "superseded_by": superseded_by,
        }

    def _remove_from_meta_cache(self, entry_id: str) -> None:
        """
        Remove an entry from the metadata cache.

        Args:
            entry_id: UUID of the entry (no-op if absent).
        """

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
            return None

        # Parse frontmatter with anchored regex (immune to --- in content)
        fm_match = _FRONTMATTER_RE.match(text)
        if not fm_match:
            logger.warning("No valid frontmatter in %s", filepath)
            return None

        try:
            meta = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as exc:
            logger.warning("YAML parse error in %s: %s", filepath, exc)
            return None

        if not isinstance(meta, dict):
            logger.warning("Frontmatter is not a dict in %s", filepath)
            return None

        content = fm_match.group(2).strip()

        return {
            "id": str(meta.get("id", "")),
            "title": str(meta.get("title", "")),
            "tags": _normalize_tags(meta.get("tags", [])),
            "type": str(meta.get("type", "")),
            "resource": str(meta.get("resource", "")),
            "part_of": _normalize_part_of(meta.get("part_of", [])),
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
        if entry.get("part_of"):
            fm_dict["part_of"] = entry["part_of"]
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
            tmp.unlink(missing_ok=True)
            raise

        logger.info("Wrote entry %s to %s", entry["id"], filepath)
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
            return True

        logger.warning("File not found for deletion: %s", filepath)
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

        return extract_relations(content)

    # -----------------------------------------------------------------------
    # CRUD operations (file + backend)
    # -----------------------------------------------------------------------

    def _suggest_links(
        self, entry_id: str, title: str, content: str
    ) -> list[dict[str, Any]]:
        """
        Suggest kb:// links to existing entries similar to this one.

        Reuses the embedding infrastructure already computed for search
        indexing to surface entries worth cross-referencing on `remember` —
        suggestions only, never auto-added; the caller decides whether to
        add the link.

        Args:
            entry_id: UUID of the entry just written (excluded from results).
            title: Entry title.
            content: Entry body.

        Returns:
            List of dicts with id, title, score, most similar first. Entries
            already linked via an explicit kb:// reference in content are
            excluded. Empty when the embedding model is unavailable or
            nothing clears the similarity floor.
        """

        embedding = self._backend.embed(f"{title}\n{content}")
        if embedding is None:
            return []

        already_linked = {rel["target"] for rel in extract_relations(content)}
        candidates = self._backend.find_similar_by_embedding(
            embedding, exclude_id=entry_id
        )

        return [
            {
                "id": candidate["id"],
                "title": self._resolve_title(candidate["id"]),
                "score": candidate["score"],
            }
            for candidate in candidates
            if candidate["id"] not in already_linked
        ]

    def _check_write_gate(
        self, title: str, content: str
    ) -> dict[str, Any] | None:
        """
        Find a live near-duplicate of a would-be new entry.

        Compares the new entry's embedding against all stored ones and
        returns the closest non-superseded match at or above
        WRITE_GATE_MIN_SIMILARITY. Superseded entries don't block —
        they're history, and re-stating an old fact is legitimate. Only
        this layer knows which entries are superseded, so the backend is
        asked for WRITE_GATE_CANDIDATES matches rather than a handful:
        a fact versioned several times leaves a stack of near-identical
        old copies that would otherwise hide the live duplicate.

        Args:
            title: New entry's title.
            content: New entry's body.

        Returns:
            Dict with id, title, score of the blocking entry, or None
            when nothing clears the threshold (or the embedding model
            is unavailable — the gate degrades open, never blocking
            writes on infrastructure failure).
        """

        embedding = self._backend.embed(f"{title}\n{content}")
        if embedding is None:
            return None

        candidates = self._backend.find_similar_by_embedding(
            embedding,
            exclude_id="",
            limit=WRITE_GATE_CANDIDATES,
            min_similarity=WRITE_GATE_MIN_SIMILARITY,
        )
        for candidate in candidates:
            meta = self._meta_cache.get(candidate["id"])
            if meta and meta.get("superseded_by"):
                continue
            return {
                "id": candidate["id"],
                "title": self._resolve_title(candidate["id"]),
                "score": candidate["score"],
            }

        return None

    def _index_entry(self, entry: dict[str, Any]) -> None:
        """
        Index an entry, carrying its hubs' titles into the search text.

        The project name lives in the hub's title, not the member entry —
        once membership is a UUID field instead of a project tag, the
        member would stop matching queries that name the project. Feeding
        the resolved hub titles to the backend keeps that keyword signal.

        Args:
            entry: Full entry dict about to be indexed.
        """

        entry["part_of_titles"] = [
            self._resolve_title(target) for target in entry.get("part_of", [])
        ]
        self._backend.index(entry)

    def _part_of_error(self, part_of: list[str]) -> str | None:
        """
        Validate the format of part_of targets.

        Args:
            part_of: Hub UUIDs as passed by the caller.

        Returns:
            An error message naming the first malformed id, or None.
        """

        for target in part_of:
            if not _validate_entry_id(target):
                return f"Invalid part_of target: {target} (must be an entry UUID)"

        return None

    def _part_of_warnings(self, part_of: list[str]) -> list[str]:
        """
        Flag part_of targets that exist but cannot hold members.

        Existence itself is not enforced here — a dangling target is
        doctor's finding, not a write error (decision f8cc3b7b).

        Args:
            part_of: Normalized hub UUIDs.

        Returns:
            One warning per known target whose type is not a
            membership target (e.g. pointing part_of at a decision).
        """

        target_types = self._schema.types_where("membership_target", True)
        warnings: list[str] = []
        for target in part_of:
            meta = self._meta_cache.get(target)
            if meta and meta.get("type", "") not in target_types:
                warnings.append(
                    f"part_of target {target} is a "
                    f"'{meta.get('type', '') or 'untyped'}' entry — expected "
                    f"one of: {', '.join(sorted(target_types)) or '(none)'}."
                )

        return warnings

    def _supersede(
        self,
        old_entry: dict[str, Any],
        title: str,
        content: str,
        tags: list[str],
        entry_type: str,
        resource: str | None,
        part_of: list[str] | None = None,
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
            resource: New entry's resource. None inherits the old
                version's — a new version of the same fact describes the
                same asset unless told otherwise.
            part_of: New entry's hub memberships. None inherits the old
                version's, same reasoning as resource.

        Returns:
            Dict with id, title, action='superseded', previous_id.
        """

        old_id = old_entry["id"]
        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        old_entry["superseded_by"] = new_id
        self._write_entry(old_entry)
        self._index_entry(old_entry)
        self._update_meta_cache(
            old_id,
            old_entry["title"],
            old_entry["tags"],
            old_entry.get("type", ""),
            part_of=old_entry.get("part_of", []),
            superseded_by=new_id,
        )

        new_part_of = (
            old_entry.get("part_of", []) if part_of is None else part_of
        )
        new_entry = {
            "id": new_id,
            "title": title,
            "tags": tags,
            "type": entry_type,
            "resource": old_entry.get("resource", "") if resource is None else resource,
            "part_of": new_part_of,
            "valid_at": now,
            "supersedes": old_id,
            "content": content,
        }
        self._write_entry(new_entry)
        self._index_entry(new_entry)
        self._update_meta_cache(new_id, title, tags, entry_type, part_of=new_part_of)

        logger.info("Entry %s superseded by %s: %s", old_id, new_id, title)
        response = {
            "id": new_id,
            "title": title,
            "action": "superseded",
            "previous_id": old_id,
        }
        suggestions = self._suggest_links(new_id, title, content)
        if suggestions:
            response["suggested_links"] = suggestions
        target_warnings = self._part_of_warnings(new_part_of)
        if target_warnings:
            response["warnings"] = target_warnings

        return response

    def remember(
        self,
        title: str,
        content: str,
        tags: list[str],
        entry_type: str,
        entry_id: str | None = None,
        force: bool = False,
        resource: str | None = None,
        supersede: bool = False,
        part_of: list[str] | None = None,
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
            resource: Canonical URI for the underlying asset. None leaves
                an updated entry's existing resource alone — a content-only
                edit must not silently drop a field its type requires.
                Pass "" to clear it deliberately.
            supersede: When updating an existing entry (via entry_id or
                duplicate match), create a new version instead of overwriting
                in place. The old entry is kept with `superseded_by` set to
                the new entry's id; the new entry gets `supersedes` pointing
                back and a fresh `valid_at`. No-op if there is no existing
                entry to supersede (falls through to plain creation).
            part_of: Hub UUIDs this entry is a member of. Required when
                creating an entry whose type declares membership
                'required'. None leaves an updated entry's memberships
                alone (same contract as resource); pass [] to clear them.

        Returns:
            Dict with id, title, and action ('created', 'updated', or
            'superseded'); an 'error' if entry_type is missing, if a
            membership-required type is created without part_of, or if the
            new entry is a semantic near-duplicate of a live entry (the
            error carries duplicate_of/similarity — resolve by updating
            that entry, superseding it, or passing force=True). Includes
            'suggested_links' (list of {id, title, score}) when other
            entries are similar enough to be worth a kb:// cross-reference —
            suggestions only, never auto-added; omitted when there are none.
        """

        if not entry_type or not entry_type.strip():
            logger.warning("Remember rejected — entry_type is required")
            return {"error": "entry_type is required"}

        tags = _normalize_tags(tags)

        if part_of is not None:
            part_of = _normalize_part_of(part_of)
            part_of_error = self._part_of_error(part_of)
            if part_of_error:
                logger.warning("Remember rejected — %s", part_of_error)
                return {"error": part_of_error}

        # Case 1: explicit ID -> update
        if entry_id:
            if not _validate_entry_id(entry_id):
                logger.warning("Invalid entry_id rejected: %s", entry_id)
                return {"error": f"Invalid entry_id: {entry_id}"}

            existing = self.get(entry_id)
            if not existing:
                logger.warning("Remember failed — entry %s not found", entry_id)
                return {"error": f"Entry {entry_id} not found"}

            if supersede:
                # New version, old entry preserved as history
                return self._supersede(
                    existing, title, content, tags, entry_type, resource, part_of
                )

            existing["title"] = title
            existing["content"] = content
            existing["tags"] = tags
            existing["type"] = entry_type
            if resource is not None:
                existing["resource"] = resource
            if part_of is not None:
                existing["part_of"] = part_of
            if not existing.get("valid_at"):
                existing["valid_at"] = datetime.now(timezone.utc).isoformat()

            self._write_entry(existing)
            self._index_entry(existing)
            self._update_meta_cache(
                entry_id, title, tags, entry_type,
                part_of=existing.get("part_of", []),
            )

            logger.info("Updated entry %s: %s", entry_id, title)
            response = {"id": entry_id, "title": title, "action": "updated"}
            suggestions = self._suggest_links(entry_id, title, content)
            if suggestions:
                response["suggested_links"] = suggestions
            target_warnings = self._part_of_warnings(existing.get("part_of", []))
            if target_warnings:
                response["warnings"] = target_warnings

            return response

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
                            best_entry, title, content, tags, entry_type,
                            resource, part_of
                        )

                    best_entry["title"] = title
                    best_entry["content"] = content
                    best_entry["tags"] = tags
                    best_entry["type"] = entry_type
                    if resource is not None:
                        best_entry["resource"] = resource
                    if part_of is not None:
                        best_entry["part_of"] = part_of
                    if not best_entry.get("valid_at"):
                        best_entry["valid_at"] = datetime.now(timezone.utc).isoformat()

                    self._write_entry(best_entry)
                    self._index_entry(best_entry)
                    self._update_meta_cache(
                        best["id"], title, tags, entry_type,
                        part_of=best_entry.get("part_of", []),
                    )

                    logger.info(
                        "Updated existing entry %s (similarity %d%%): %s",
                        best["id"],
                        best["score"],
                        title,
                    )
                    response = {
                        "id": best["id"],
                        "title": title,
                        "action": "updated",
                        "matched": best["title"],
                        "similarity": best["score"],
                    }
                    suggestions = self._suggest_links(best["id"], title, content)
                    if suggestions:
                        response["suggested_links"] = suggestions
                    target_warnings = self._part_of_warnings(
                        best_entry.get("part_of", [])
                    )
                    if target_warnings:
                        response["warnings"] = target_warnings

                    return response

        # Case 3: create new. Membership enforcement is create-only:
        # rejecting updates too would freeze every pre-part_of entry
        # until the migration lands (those get a warning via check_entry
        # instead). Checked before the write-gate — no point embedding
        # an entry that is structurally invalid anyway.
        rule = self._schema.rule(entry_type)
        if rule is not None and rule.membership == "required" and not part_of:
            logger.warning(
                "Remember rejected — type '%s' requires part_of", entry_type
            )
            return {
                "error": (
                    f"entry_type '{entry_type}' requires part_of: pass the "
                    "UUID(s) of the hub entry the new entry belongs to "
                    "(find it via search/list, or create the hub first)."
                )
            }

        # Semantic write-gate. Title-based
        # dedup above catches rewordings of the same title; this catches
        # the same fact written under a different title. force skips the
        # gate outright; supersede signals a deliberate new version, so
        # it passes too (matched via entry_id/title in the cases above).
        if not force and not supersede:
            duplicate = self._check_write_gate(title, content)
            if duplicate:
                logger.warning(
                    "Remember rejected — near-duplicate of %s (similarity %.2f)",
                    duplicate["id"],
                    duplicate["score"],
                )
                return {
                    "error": (
                        "Near-duplicate of existing entry "
                        f"'{duplicate['title']}' ({duplicate['id']}, cosine "
                        f"{duplicate['score']:.2f}). Update it via "
                        "entry_id=<that id> (optionally supersede=True for a "
                        "new version), or pass force=True to write anyway."
                    ),
                    "duplicate_of": duplicate["id"],
                    "similarity": duplicate["score"],
                }

        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "title": title,
            "tags": tags,
            "type": entry_type,
            "resource": resource or "",
            "part_of": part_of or [],
            "valid_at": datetime.now(timezone.utc).isoformat(),
            "content": content,
        }

        self._write_entry(entry)
        self._index_entry(entry)
        self._update_meta_cache(
            entry_id, title, tags, entry_type, part_of=part_of or []
        )

        logger.info("Created new entry %s: %s", entry_id, title)
        response = {"id": entry_id, "title": title, "action": "created"}
        suggestions = self._suggest_links(entry_id, title, content)
        if suggestions:
            response["suggested_links"] = suggestions
        target_warnings = self._part_of_warnings(part_of or [])
        if target_warnings:
            response["warnings"] = target_warnings

        return response

    def get(
        self,
        entry_id: str,
        with_relations: bool = False,
        relations_limit: int | None = None,
        record_access: bool = False,
        digest: bool = False,
        hops: int = 1,
    ) -> dict[str, Any] | None:
        """
        Read the full content of an entry.

        Args:
            entry_id: UUID of the entry.
            with_relations: Include graph relations (outgoing and incoming links).
            relations_limit: Optional per-direction cap on relations —
                newest-first. None returns everything.
            record_access: Bump the entry's usage counters (staleness
                signal). Only deliberate reads — recall — should set this;
                internal lookups and dashboard views must not.
            digest: Allow the per-type back-link digest for types whose
                schema rule sets digest_on_recall. Set by `recall`, where
                the cap on a hub's 86 back-links actually bites; callers
                that read every relation anyway (the dashboard) leave it
                off and keep the flat list.
            hops: 1 (default) or 2 — see SQLiteBackend.get_relations. On a
                digesting type, applies to the 'out' list only.

        Returns:
            Entry dict or None if not found. When with_relations is True, includes
            a 'relations' key with 'out' and 'in' lists plus a
            'relations_truncated' flag when a cap (at either hop) cut the
            lists short. With digest enabled on a digesting type, 'in' is
            replaced by 'in_digest'.
        """

        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            return None

        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            return None

        entry = self._read_entry(filepath)
        if not entry:
            # Unparseable
            return None

        # Append graph relations if requested — types flagged
        # digest_on_recall get a per-type summary of their back-links
        # instead of an arbitrarily truncated flat list.
        if with_relations:
            rule = self._schema.rule(entry.get("type", ""))
            if digest and rule is not None and rule.digest_on_recall:
                relations = self.digest_relations(
                    entry_id, out_limit=relations_limit, hops=hops
                )
                truncated = len(relations["out"]) < relations["out_total"]
            else:
                relations = self.get_relations(
                    entry_id, limit=relations_limit, hops=hops
                )
                truncated = (
                    len(relations["out"]) < relations["out_total"]
                    or len(relations["in"]) < relations["in_total"]
                )
            if relations.get("hop2_truncated"):
                truncated = True
            entry["relations"] = relations
            if truncated:
                entry["relations_truncated"] = True

        if record_access:
            self._backend.record_access([entry_id])

        return entry

    def get_relations(
        self, entry_id: str, limit: int | None = None, hops: int = 1
    ) -> dict[str, Any]:
        """
        Get all graph relations for an entry (outgoing and incoming).

        Delegates to the search backend for raw relation data (id + type),
        then resolves titles from the metadata cache or Markdown files.

        Args:
            entry_id: UUID of the entry.
            limit: Optional per-direction cap, newest-first (see
                SQLiteBackend.get_relations). None returns everything.
            hops: 1 (default) or 2 — see SQLiteBackend.get_relations.

        Returns:
            Dict with 'out' list (outgoing) and 'in' list (incoming/backlinks)
            — each item has 'type', 'id', 'edge', 'hops' — plus
            'out_total'/'in_total' uncapped counts, and (at hops=2)
            'hop2_total'/'hop2_truncated'. Hop-1 items also carry 'title';
            hop-2 items carry 'via' instead — no title resolution, since a
            hop-2 item is a navigation breadcrumb (see it via 'via', then
            recall it directly for content), not a result to read as-is.
        """

        empty: dict[str, Any] = {"out": [], "in": [], "out_total": 0, "in_total": 0}
        if not _validate_entry_id(entry_id):
            logger.warning("Invalid entry_id rejected: %s", entry_id)
            return empty

        # Get raw relations from backend (id + type only)
        raw = self._backend.get_relations(entry_id, limit=limit, hops=hops)

        def _resolve(rel: dict[str, Any]) -> dict[str, Any]:
            item = {
                "type": rel["type"],
                "id": rel["id"],
                "edge": rel["edge"],
                "hops": rel["hops"],
            }
            if rel["hops"] == 1:
                item["title"] = self._resolve_title(rel["id"])
            else:
                item["via"] = rel["via"]
            return item

        result: dict[str, Any] = {
            "out": [_resolve(rel) for rel in raw["out"]],
            "in": [_resolve(rel) for rel in raw["in"]],
            "out_total": raw["out_total"],
            "in_total": raw["in_total"],
        }
        if "hop2_total" in raw:
            result["hop2_total"] = raw["hop2_total"]
            result["hop2_truncated"] = raw["hop2_truncated"]

        return result

    def digest_relations(
        self, entry_id: str, out_limit: int | None = None, hops: int = 1
    ) -> dict[str, Any]:
        """
        Summarize an entry's incoming relations by the linking entry's type.

        Written for hubs: with 86 back-links, a flat list capped at 20 is
        both incomplete and arbitrary (the cap keeps the most recently
        *indexed* links, and index order is reshuffled by every rebuild).
        A per-type digest reports the whole picture instead, and `list`
        with an entry_type filter expands any bucket in full.

        Args:
            entry_id: UUID of the entry.
            out_limit: Optional cap on the outgoing list, which stays
                flat — a hub's own curated links are few.
            hops: 1 (default) or 2 — applies to the outgoing list only.
                `in_digest` is always built from direct (hop-1) back-links;
                mixing hop-2 arrivals into an aggregate meant to answer
                "who links to this hub" would misrepresent entries that
                never link to the hub at all, only to one of its members.

        Returns:
            Dict with 'out' (flat, as usual — hop-2 items carry 'hops'/
            'via' like get_relations), 'in_digest' mapping the linking
            entry's type to {count, sample}, and the uncapped
            'out_total'/'in_total' counts (plus 'hop2_total'/
            'hop2_truncated' at hops=2). Members come from kb:// back-links
            AND from part_of memberships pointing here — during the
            [Back to hub] -> part_of migration either channel alone would
            show only half the picture. 'count' counts entries, not edges
            — one entry both linking here and a member via part_of is one
            member. 'sample' is a few example members, in no defined
            order; read a bucket in full with
            list(part_of=[this id], entry_type=...).
        """

        relations = self.get_relations(entry_id, hops=hops)

        digest: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()

        def _count_member(member_id: str, title: str) -> None:
            if member_id in seen:
                # Same entry, another edge/channel — already counted
                return
            seen.add(member_id)

            meta = self._meta_cache.get(member_id, {})
            source_type = meta.get("type", "") or "untyped"
            bucket = digest.setdefault(source_type, {"count": 0, "sample": []})
            bucket["count"] += 1
            if len(bucket["sample"]) < DIGEST_SAMPLE_PER_TYPE:
                bucket["sample"].append({"id": member_id, "title": title})

        for relation in relations["in"]:
            if relation.get("hops", 1) != 1:
                # in_digest is direct-backlinks-only, never hop-2 arrivals
                continue
            _count_member(relation["id"], relation["title"])

        for member_id, meta in self._meta_cache.items():
            if entry_id in meta.get("part_of", []) and not meta.get("superseded_by"):
                _count_member(member_id, meta["title"])

        out = relations["out"]
        if out_limit is not None:
            out = out[:out_limit]

        result = {
            "out": out,
            "in_digest": digest,
            "out_total": relations["out_total"],
            "in_total": relations["in_total"],
        }
        if "hop2_total" in relations:
            result["hop2_total"] = relations["hop2_total"]
            result["hop2_truncated"] = relations["hop2_truncated"]
        return result

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
            return "(unknown)"

        # Check cache first
        cached = self._meta_cache.get(entry_id)
        if cached:
            # Title from cache
            return cached["title"]

        # Fallback to disk (entry may not be cached yet)
        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            return "(unknown)"

        entry = self._read_entry(filepath)
        if not entry:
            return "(unknown)"

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
            return None

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
            return False

        filepath = self._entries_path / f"{entry_id}.md"
        if not filepath.exists():
            logger.warning("Delete failed — entry %s not found", entry_id)
            return False

        self._delete_entry_file(entry_id)
        self._remove_from_meta_cache(entry_id)

        try:
            self._backend.unindex(entry_id)
        except sqlite3.Error:
            logger.warning("Entry %s not in index (already removed?)", entry_id)

        logger.info("Deleted entry %s", entry_id)
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
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Full-text search with optional tag and entry-type filtering.

        Delegates to the search backend for raw results (id + score),
        then enriches with title, tags, and snippets from Markdown files.

        Args:
            query_str: Search query string.
            tags: Optional list of tags to filter by (AND logic).
            limit: Maximum number of results.
            include_superseded: Include entries that have been superseded
                (have a non-empty superseded_by). Defaults to hiding them.
            entry_type: Optional entry type to filter by (exact match,
                e.g. "diagnostic").
            part_of: Optional hub UUIDs to filter by — only entries that
                are members of all of them (AND logic, like tags).

        Returns:
            List of dicts with id, title, tags, type, snippet, score.
        """

        normalized_tags = _normalize_tags(tags) if tags else None
        normalized_part_of = _normalize_part_of(part_of) if part_of else None

        # Over-fetch when filtering, so trimming back to `limit` still
        # returns a full page — the backend has no notion of versioning.
        fetch_limit = limit
        if not include_superseded:
            fetch_limit = min(limit * SUPERSEDED_FETCH_MULTIPLIER, SUPERSEDED_FETCH_CAP)

        raw_results = self._backend.search(
            query_str,
            normalized_tags,
            fetch_limit,
            entry_type=entry_type,
            part_of=normalized_part_of,
        )

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

                result = {
                    "id": entry["id"],
                    "title": entry["title"],
                    "tags": entry["tags"],
                    "type": entry.get("type", ""),
                    "snippet": snippet,
                    "score": hit["score"],
                }
                # Usage/staleness signal from the backend, when present
                if "access_count" in hit:
                    result["access_count"] = hit["access_count"]
                    result["staleness"] = hit.get("staleness")
                results.append(result)

            if len(results) >= limit:
                break

        logger.info("Search '%s' returned %d results", query_str, len(results))
        return results

    def iter_entries(self) -> list[dict[str, Any]]:
        """
        Read every valid entry from disk, in filename order.

        The Markdown files are the source of truth, so both `rebuild` and
        `doctor` read them here rather than going through the index.

        Returns:
            List of parsed entry dicts. Unparseable files are skipped
            with a warning.
        """

        entries: list[dict[str, Any]] = []
        for filepath in sorted(self._entries_path.glob("*.md")):
            entry = self._read_entry(filepath)
            if entry and entry["id"]:
                entries.append(entry)
            else:
                logger.warning("Skipped invalid entry: %s", filepath)

        return entries

    def doctor(self) -> dict[str, Any]:
        """
        Run the schema-driven integrity pass over all entries.

        Returns:
            Report dict from doctor.run_doctor — 'entries_scanned',
            'max_degree', and per-kind 'checks'.
        """

        # Validate the source of truth against the schema; usage counters
        # are index-only, so the cleanup-candidate check needs a snapshot
        return run_doctor(
            self.iter_entries(), self._schema, usage=self._backend.get_usage_snapshot()
        )

    def rebuild(self) -> dict[str, Any]:
        """
        Rebuild the search index from all Markdown files.

        Reads all valid entry files, collects them, and passes the full
        list to the backend for a single-pass rebuild. Also runs the
        `doctor` integrity pass (non-blocking) over the same entries.

        Returns:
            Dict with 'count' (entries indexed) and 'report' — the full
            doctor report for the rebuilt knowledge base.
        """

        logger.info("Rebuilding index from %s", self._entries_path)

        entries = self.iter_entries()

        # Same hub-title enrichment as _index_entry, resolved against the
        # files being indexed rather than the (possibly stale) meta cache
        titles = {entry["id"]: entry["title"] for entry in entries}
        for entry in entries:
            entry["part_of_titles"] = [
                titles.get(target, "") for target in entry.get("part_of", [])
            ]

        # Delegate bulk indexing to backend
        count = self._backend.rebuild(entries)

        # Reload metadata cache to stay in sync
        self._load_meta_cache()

        # Integrity check (non-blocking, informational only). Usage
        # counters survive rebuild, so the snapshot read after
        # backend.rebuild() above reflects carried-over access history.
        report = run_doctor(
            entries, self._schema, usage=self._backend.get_usage_snapshot()
        )

        logger.info("Rebuild complete: %d entries indexed", count)
        return {"count": count, "report": report}

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
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        List entries sorted by title, with optional tag and type filters.

        Reads from the in-memory metadata cache instead of scanning
        files on disk.

        Args:
            tags: Optional list of tags to filter by (AND logic).
            limit: Maximum number of entries.
            include_superseded: Include entries that have been superseded
                (have a non-empty superseded_by). Defaults to hiding them.
            entry_type: Optional exact type filter — deliberately not
                restricted to the schema, so types left over from before
                a schema change can still be listed and cleaned up.
            part_of: Optional hub UUIDs — only entries that are members
                of all of them (AND logic, like tags).

        Returns:
            List of dicts with id, title, tags, type, part_of.
        """

        filter_tags = set(_normalize_tags(tags)) if tags else None
        filter_part_of = set(_normalize_part_of(part_of)) if part_of else None

        entries = []
        for entry_id, meta in self._meta_cache.items():
            if filter_tags and not filter_tags.issubset(set(meta["tags"])):
                continue

            if entry_type and meta.get("type", "") != entry_type:
                continue

            if filter_part_of and not filter_part_of.issubset(
                set(meta.get("part_of", []))
            ):
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
                    "part_of": meta.get("part_of", []),
                }
            )

        entries.sort(key=lambda e: e["title"].lower())

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

        return result

    def log_query_event(self, **fields: Any) -> None:
        """
        Record one search/recall/remember call for usage analytics.

        Thin passthrough to the backend — see SQLiteBackend.log_query_event
        for the field list.
        """

        self._backend.log_query_event(**fields)

    def get_analytics_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        """
        Aggregate query_log + entries into the /api/analytics metric set.

        Thin passthrough to the backend — see SQLiteBackend.get_analytics_snapshot.
        """

        return self._backend.get_analytics_snapshot(**kwargs)

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
            (source_id, target_id, type, edge). Membership (`part_of`)
            is included as an edge with type/edge both 'part_of' — since
            the [Back to hub] -> part_of migration, that structural field
            is a member's only connection to its hub for entries whose
            kb:// back-link was stripped, so omitting it here would leave
            every migrated entry drawn as an isolated node.
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

        # part_of membership edges. Skip a pair already connected by a
        # kb:// link (e.g. `pattern` entries, which keep their hub link
        # as a semantic edge rather than migrating it to part_of) so the
        # same hub connection isn't drawn twice.
        linked_pairs = {(rel["source_id"], rel["target_id"]) for rel in edges}
        for entry_id in node_ids:
            for hub_id in self._meta_cache[entry_id].get("part_of", []):
                if hub_id in node_ids and (entry_id, hub_id) not in linked_pairs:
                    edges.append(
                        {
                            "source_id": entry_id,
                            "target_id": hub_id,
                            "type": "part_of",
                            "edge": "part_of",
                        }
                    )

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
        return []

    normalized = []
    for tag in tags:
        clean = str(tag).lower().strip()
        if clean:
            normalized.append(clean)

    return sorted(set(normalized))


def _normalize_part_of(part_of: Any) -> list[str]:
    """
    Normalize a part_of list: lowercase, strip, dedupe, reject empty.

    Format validation (well-formed UUIDs) is a separate, rejectable
    concern — see KnowledgeBase._part_of_error.

    Args:
        part_of: Raw part_of input (list or other).

    Returns:
        Cleaned, sorted list of target id strings.
    """

    if not isinstance(part_of, list):
        return []

    cleaned = {
        str(target).lower().strip()
        for target in part_of
        if str(target).strip()
    }

    return sorted(cleaned)
