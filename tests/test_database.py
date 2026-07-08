"""
Tests for the KnowledgeBase class.

Exercises all CRUD, search, tag, relation, and edge-case paths against an
isolated tmp_path directory — never touches /opt/knowledge.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import KnowledgeBase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    """Provide a fresh KnowledgeBase rooted in a temporary directory."""
    return KnowledgeBase(str(tmp_path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_entry(
    kb: KnowledgeBase,
    title: str = "Test Entry",
    content: str = "Some content for testing.",
    tags: list[str] | None = None,
    *,
    force: bool = False,
    entry_type: str = "snippet",
) -> dict:
    """Shorthand for creating an entry and asserting success."""

    if tags is None:
        tags = ["test"]

    result = kb.remember(title, content, tags, entry_type, force=force)
    assert "error" not in result, f"Unexpected error: {result}"
    # Created or updated
    return result


# ===========================================================================
# Basic CRUD — remember / get / delete
# ===========================================================================


class TestRememberAndGet:
    """Tests for the remember (upsert) and get (read) operations."""

    def test_remember_create(self, kb: KnowledgeBase) -> None:
        """New title with no existing entry creates a new entry."""

        result = kb.remember("New Entry", "Body text.", ["infra"], "snippet")

        assert result["action"] == "created"
        assert result["title"] == "New Entry"
        assert "id" in result

        # Verify entry is readable
        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["title"] == "New Entry"
        assert entry["content"] == "Body text."
        assert entry["tags"] == ["infra"]

    def test_remember_update_by_id(self, kb: KnowledgeBase) -> None:
        """Providing entry_id updates the existing entry in place."""

        created = _create_entry(kb, "Original Title", "Original body.")
        entry_id = created["id"]

        updated = kb.remember(
            "Updated Title",
            "Updated body.",
            ["updated"],
            "snippet",
            entry_id=entry_id,
        )

        assert updated["action"] == "updated"
        assert updated["id"] == entry_id

        # Verify content changed on disk
        entry = kb.get(entry_id)
        assert entry is not None
        assert entry["title"] == "Updated Title"
        assert entry["content"] == "Updated body."
        assert entry["tags"] == ["updated"]

    def test_remember_update_by_id_not_found(self, kb: KnowledgeBase) -> None:
        """Updating with a nonexistent entry_id returns an error."""

        fake_id = str(uuid.uuid4())
        result = kb.remember("Title", "Content.", ["tag"], "snippet", entry_id=fake_id)

        assert "error" in result
        assert fake_id in result["error"]

    def test_remember_missing_type(self, kb: KnowledgeBase) -> None:
        """Calling remember without entry_type returns an error."""

        result = kb.remember("Title", "Content.", ["tag"], "")

        assert "error" in result
        assert "entry_type" in result["error"]

    def test_remember_upsert_by_title(self, kb: KnowledgeBase) -> None:
        """Calling remember twice with the same title updates the first entry."""

        first = kb.remember("Duplicate Title", "First body.", ["v1"], "snippet")
        second = kb.remember("Duplicate Title", "Second body.", ["v2"], "snippet")

        assert first["action"] == "created"
        assert second["action"] == "updated"
        # Must reuse the same UUID
        assert second["id"] == first["id"]

        # Verify only the updated content is stored
        entry = kb.get(first["id"])
        assert entry is not None
        assert entry["content"] == "Second body."
        assert entry["tags"] == ["v2"]

    def test_remember_force_create(self, kb: KnowledgeBase) -> None:
        """force=True skips duplicate detection and creates a new entry."""

        first = _create_entry(kb, "Same Title", "First.")
        second = kb.remember("Same Title", "Second.", ["tag"], "snippet", force=True)

        assert second["action"] == "created"
        # Must be a different UUID
        assert second["id"] != first["id"]

    def test_get_not_found(self, kb: KnowledgeBase) -> None:
        """Getting a nonexistent UUID returns None."""

        result = kb.get(str(uuid.uuid4()))

        # Not found
        assert result is None

    def test_delete(self, kb: KnowledgeBase) -> None:
        """Deleting an existing entry succeeds and removes it from get."""

        created = _create_entry(kb)
        entry_id = created["id"]

        deleted = kb.delete(entry_id)
        assert deleted is True

        # Entry must be gone
        assert kb.get(entry_id) is None

    def test_delete_not_found(self, kb: KnowledgeBase) -> None:
        """Deleting a nonexistent UUID returns False."""

        result = kb.delete(str(uuid.uuid4()))

        # Not found
        assert result is False


# ===========================================================================
# Tags
# ===========================================================================


class TestTags:
    """Tests for tag normalization, listing, and filtering."""

    def test_tags_normalized(self, kb: KnowledgeBase) -> None:
        """Tags are lowercased, stripped, sorted, and deduplicated."""

        result = _create_entry(
            kb,
            "Tag Test",
            "Content.",
            ["  Infra ", "INFRA", "deploy", "Deploy"],
        )

        entry = kb.get(result["id"])
        assert entry is not None
        # Should be lowercase, sorted, deduplicated
        assert entry["tags"] == ["deploy", "infra"]

    def test_list_tags(self, kb: KnowledgeBase) -> None:
        """list_tags returns correct per-tag counts."""

        _create_entry(kb, "Entry A", "A.", ["infra", "linux"], force=True)
        _create_entry(kb, "Entry B", "B.", ["infra", "docker"], force=True)
        _create_entry(kb, "Entry C", "C.", ["docker"], force=True)

        tags = kb.list_tags()
        tag_map = {t["tag"]: t["count"] for t in tags}

        assert tag_map["infra"] == 2
        assert tag_map["docker"] == 2
        assert tag_map["linux"] == 1

    def test_list_entries_tag_filter(self, kb: KnowledgeBase) -> None:
        """list_entries with tag filter returns only matching entries."""

        _create_entry(kb, "Entry A", "A.", ["infra", "linux"], force=True)
        _create_entry(kb, "Entry B", "B.", ["infra", "docker"], force=True)
        _create_entry(kb, "Entry C", "C.", ["docker"], force=True)

        # Filter by "infra"
        entries = kb.list_entries(tags=["infra"])
        assert len(entries) == 2
        titles = {e["title"] for e in entries}
        assert titles == {"Entry A", "Entry B"}

        # Filter by "docker" — should include B and C
        entries = kb.list_entries(tags=["docker"])
        assert len(entries) == 2
        titles = {e["title"] for e in entries}
        assert titles == {"Entry B", "Entry C"}

        # Filter by both "infra" AND "docker" — only B matches
        entries = kb.list_entries(tags=["infra", "docker"])
        assert len(entries) == 1
        assert entries[0]["title"] == "Entry B"


# ===========================================================================
# Search
# ===========================================================================


class TestSearch:
    """Tests for full-text search with SQLite FTS5."""

    def test_search_basic(self, kb: KnowledgeBase) -> None:
        """Searching by a keyword finds the matching entry."""

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        results = kb.search("playbook")

        assert len(results) >= 1
        assert any(r["id"] for r in results)
        assert any(r["title"] == "Ansible Playbook Guide" for r in results)
        # Every result must include a score
        for r in results:
            assert "score" in r

    def test_search_no_results(self, kb: KnowledgeBase) -> None:
        """Searching for a term not in any entry returns empty."""

        _create_entry(kb, "Some Entry", "Regular content.")

        results = kb.search("xylophone")

        # Nothing found
        assert results == []

    def test_search_tag_filter(self, kb: KnowledgeBase) -> None:
        """Search with tag filter only returns entries matching the tag."""

        _create_entry(kb, "Docker Networking", "Bridge mode.", ["docker"], force=True)
        _create_entry(
            kb, "Docker Volumes", "Bind mounts.", ["docker", "storage"], force=True
        )
        _create_entry(
            kb, "Ansible Networking", "Network modules.", ["ansible"], force=True
        )

        # Search "networking" but only in "docker" tag
        results = kb.search("networking", tags=["docker"])

        titles = {r["title"] for r in results}
        assert "Docker Networking" in titles
        # Ansible entry must not appear
        assert "Ansible Networking" not in titles

    def test_search_french_stemming(self, kb: KnowledgeBase) -> None:
        """French stemmer matches plural/singular forms (serveurs -> serveur)."""

        _create_entry(kb, "Configuration serveur", "Le serveur principal.")

        # Search with plural form — stemmer should match
        results = kb.search("serveurs")

        assert len(results) >= 1
        assert results[0]["title"] == "Configuration serveur"


# ===========================================================================
# Duplicate detection
# ===========================================================================


class TestDuplicateDetection:
    """Tests for find_similar title matching."""

    def test_find_similar(self, kb: KnowledgeBase) -> None:
        """Similar titles are detected above the threshold."""

        _create_entry(kb, "Ansible Deployment Playbook", "Content.")

        similar = kb.find_similar("Ansible Deployment Playbooks")

        assert len(similar) >= 1
        assert similar[0]["title"] == "Ansible Deployment Playbook"
        assert similar[0]["score"] >= 75

    def test_find_similar_no_match(self, kb: KnowledgeBase) -> None:
        """Completely different titles return no matches."""

        _create_entry(kb, "Ansible Deployment Playbook", "Content.")

        similar = kb.find_similar("Docker Container Networking")

        # Nothing similar
        assert similar == []


# ===========================================================================
# Relations
# ===========================================================================


class TestRelations:
    """Tests for kb:// link extraction and relation graph."""

    def test_extract_relations_basic(self) -> None:
        """kb://uuid#type links are extracted correctly."""

        target_id = str(uuid.uuid4())
        content = f"See [related article](kb://{target_id}#depends_on) for details."

        relations = KnowledgeBase._extract_relations(content)

        assert len(relations) == 1
        assert relations[0]["target"] == target_id
        assert relations[0]["type"] == "depends_on"

    def test_extract_relations_no_fragment(self) -> None:
        """kb://uuid without #type defaults to 'related'."""

        target_id = str(uuid.uuid4())
        content = f"See [other](kb://{target_id}) for context."

        relations = KnowledgeBase._extract_relations(content)

        assert len(relations) == 1
        assert relations[0]["target"] == target_id
        assert relations[0]["type"] == "related"

    def test_extract_relations_dedup(self) -> None:
        """Duplicate kb:// links with the same target+type are deduplicated."""

        target_id = str(uuid.uuid4())
        content = (
            f"First [link](kb://{target_id}#ref) and "
            f"second [link](kb://{target_id}#ref)."
        )

        relations = KnowledgeBase._extract_relations(content)

        # Only one relation after dedup
        assert len(relations) == 1

    def test_extract_relations_none(self) -> None:
        """Content without any kb:// links returns an empty list."""

        content = (
            "No knowledge base links here. Just a [regular link](https://example.com)."
        )

        relations = KnowledgeBase._extract_relations(content)

        # Empty
        assert relations == []

    def test_get_with_relations(self, kb: KnowledgeBase) -> None:
        """get(with_relations=True) includes outgoing relations."""

        target = _create_entry(kb, "Target Entry", "Target content.", force=True)
        target_id = target["id"]

        source = _create_entry(
            kb,
            "Source Entry",
            f"Links to [target](kb://{target_id}#depends_on).",
            force=True,
        )
        source_id = source["id"]

        entry = kb.get(source_id, with_relations=True)

        assert entry is not None
        assert "relations" in entry

        outgoing = entry["relations"]["out"]
        assert len(outgoing) >= 1
        assert any(r["id"] == target_id for r in outgoing)
        assert any(r["type"] == "depends_on" for r in outgoing)

    def test_relations_backlinks(self, kb: KnowledgeBase) -> None:
        """A links to B => get_relations(B) includes A in incoming."""

        entry_b = _create_entry(kb, "Entry B", "I am B.", force=True)
        b_id = entry_b["id"]

        entry_a = _create_entry(
            kb,
            "Entry A",
            f"See [B](kb://{b_id}#related).",
            force=True,
        )
        a_id = entry_a["id"]

        relations = kb.get_relations(b_id)

        incoming = relations["in"]
        assert len(incoming) >= 1
        assert any(r["id"] == a_id for r in incoming)

    def test_relations_typed(self, kb: KnowledgeBase) -> None:
        """Link #type is preserved in both outgoing and incoming relations."""

        target = _create_entry(kb, "Target", "Content.", force=True)
        target_id = target["id"]

        source = _create_entry(
            kb,
            "Source",
            f"Ref [target](kb://{target_id}#supersedes).",
            force=True,
        )

        # Outgoing from source
        source_entry = kb.get(source["id"], with_relations=True)
        assert source_entry is not None
        out_rel = source_entry["relations"]["out"]
        assert any(r["type"] == "supersedes" and r["id"] == target_id for r in out_rel)

        # Incoming on target
        target_rels = kb.get_relations(target_id)
        in_rel = target_rels["in"]
        assert any(
            r["type"] == "supersedes" and r["id"] == source["id"] for r in in_rel
        )


# ===========================================================================
# Rebuild
# ===========================================================================


class TestRebuild:
    """Tests for index rebuild from Markdown files."""

    def test_rebuild(self, kb: KnowledgeBase) -> None:
        """Rebuilding the index preserves entry count and search capability."""

        _create_entry(kb, "Alpha Entry", "Alpha content.", ["alpha"], force=True)
        _create_entry(kb, "Beta Entry", "Beta content.", ["beta"], force=True)

        result = kb.rebuild()

        assert result["count"] == 2

        # Search must still work after rebuild
        results = kb.search("alpha")
        assert len(results) >= 1
        assert results[0]["title"] == "Alpha Entry"

    def test_rebuild_conformance_warnings(self, kb: KnowledgeBase) -> None:
        """Rebuild flags legacy entries missing type, and bad resources."""

        # Simulate a legacy entry predating the type requirement (bypasses
        # remember()'s validation by writing the file directly)
        legacy_id = str(uuid.uuid4())
        kb._write_entry(
            {
                "id": legacy_id,
                "title": "Legacy",
                "tags": ["tag"],
                "type": "",
                "resource": "",
                "content": "Legacy content.",
            }
        )
        kb.remember(
            "Bad Resource",
            "Content.",
            ["tag"],
            "decision",
            force=True,
            resource="not-a-uri",
        )

        result = kb.rebuild()

        assert len(result["warnings"]["missing_type"]) == 1
        assert len(result["warnings"]["malformed_resource"]) == 1


# ===========================================================================
# List
# ===========================================================================


class TestList:
    """Tests for listing and sorting entries."""

    def test_list_entries_sorted(self, kb: KnowledgeBase) -> None:
        """Entries are returned sorted by title (case-insensitive)."""

        _create_entry(kb, "Charlie", "C.", force=True)
        _create_entry(kb, "alpha", "A.", force=True)
        _create_entry(kb, "Bravo", "B.", force=True)

        entries = kb.list_entries()

        titles = [e["title"] for e in entries]
        assert titles == ["alpha", "Bravo", "Charlie"]

    def test_list_entries_limit(self, kb: KnowledgeBase) -> None:
        """The limit parameter caps the number of returned entries."""

        for i in range(5):
            _create_entry(kb, f"Entry {i:02d}", f"Body {i}.", force=True)

        entries = kb.list_entries(limit=3)

        assert len(entries) == 3


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Tests for boundary conditions and error handling."""

    def test_remember_empty_tags(self, kb: KnowledgeBase) -> None:
        """An empty tags list is accepted without errors."""

        result = kb.remember("No Tags", "Content.", [], "snippet")

        assert result["action"] == "created"

        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["tags"] == []

    def test_remember_special_chars_in_title(self, kb: KnowledgeBase) -> None:
        """Accented characters, dashes, and special chars in titles work."""

        result = kb.remember(
            "Deploiement sur les serveurs — etat des lieux",
            "Contenu avec des accents : e, a, u.",
            ["infra"],
            "snippet",
        )

        assert result["action"] == "created"

        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["title"] == "Deploiement sur les serveurs — etat des lieux"

    def test_read_malformed_file(self, kb: KnowledgeBase, tmp_path: Path) -> None:
        """A malformed Markdown file without proper frontmatter returns None."""

        # Write a file that is missing the closing --- delimiter
        malformed = tmp_path / "entries" / "bad-file.md"
        malformed.write_text("---\ntitle: broken\n", encoding="utf-8")

        entry = kb._read_entry(malformed)

        # Graceful failure
        assert entry is None


# ===========================================================================
# Extended schema — type / resource
# ===========================================================================


class TestExtendedSchema:
    """Tests for the optional type/resource frontmatter fields."""

    def test_remember_roundtrip_new_fields(self, kb: KnowledgeBase) -> None:
        """type/resource set via remember round-trip through get."""

        result = kb.remember(
            "Hub Entry",
            "Content.",
            ["hub", "myproject"],
            entry_type="hub",
            resource="https://github.com/org/repo",
        )

        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["type"] == "hub"
        assert entry["resource"] == "https://github.com/org/repo"

    def test_legacy_entry_backward_compat(
        self, kb: KnowledgeBase, tmp_path: Path
    ) -> None:
        """A pre-existing file with only id/title/tags parses with empty defaults."""

        entry_id = str(uuid.uuid4())
        legacy_file = tmp_path / "entries" / f"{entry_id}.md"
        legacy_file.write_text(
            f"---\nid: {entry_id}\ntitle: Legacy Entry\ntags: [legacy]\n---\n\n"
            "Legacy content.\n",
            encoding="utf-8",
        )

        entry = kb._read_entry(legacy_file)

        assert entry is not None
        assert entry["type"] == ""
        assert entry["resource"] == ""

    def test_write_entry_omits_empty_resource(self, kb: KnowledgeBase) -> None:
        """type is always written; resource is omitted from disk when empty."""

        result = kb.remember("Plain Entry", "Content.", ["tag"], "snippet")

        filepath = kb.entry_path(result["id"])
        assert filepath is not None
        text = filepath.read_text(encoding="utf-8")

        assert "type: snippet" in text
        assert "resource:" not in text

    def test_remember_update_by_id_overwrites_new_fields(
        self, kb: KnowledgeBase
    ) -> None:
        """Updating by entry_id replaces type/resource like title/tags."""

        created = kb.remember(
            "Original",
            "Body.",
            ["tag"],
            entry_type="decision",
        )
        entry_id = created["id"]

        kb.remember(
            "Updated",
            "Body.",
            ["tag"],
            entry_id=entry_id,
            entry_type="diagnostic",
            resource="/local/path",
        )

        entry = kb.get(entry_id)
        assert entry is not None
        assert entry["type"] == "diagnostic"
        assert entry["resource"] == "/local/path"

    def test_list_entries_includes_new_fields(self, kb: KnowledgeBase) -> None:
        """list_entries surfaces type."""

        kb.remember(
            "Listed Entry",
            "Content.",
            ["tag"],
            entry_type="snippet",
        )

        entries = kb.list_entries()
        assert len(entries) == 1
        assert entries[0]["type"] == "snippet"

    def test_search_includes_new_fields(self, kb: KnowledgeBase) -> None:
        """search results surface type."""

        kb.remember(
            "Searchable Entry",
            "Body content about xylophones.",
            ["tag"],
            entry_type="pattern",
        )

        results = kb.search("xylophones")
        assert len(results) >= 1
        assert results[0]["type"] == "pattern"
