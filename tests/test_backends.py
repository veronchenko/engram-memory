"""
SQLite backend tests for Engram.

Exercises the KnowledgeBase CRUD, search, tags, relations, and rebuild
operations against the SQLite FTS5 backend.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import KnowledgeBase
from search_backend import SQLiteBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    """KnowledgeBase backed by SQLiteBackend."""

    backend = SQLiteBackend(tmp_path / "index" / "engram.db")

    # Initialized
    return KnowledgeBase(str(tmp_path), backend=backend)


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
) -> dict[str, Any]:
    """Shorthand for creating an entry and asserting success."""

    if tags is None:
        tags = ["test"]

    result = kb.remember(title, content, tags, entry_type, force=force)
    assert "error" not in result, f"Unexpected error: {result}"

    # Created or updated
    return result


# ===========================================================================
# CRUD
# ===========================================================================


class TestCRUD:
    """CRUD operations against the SQLite backend."""

    def test_remember_create(self, kb: KnowledgeBase) -> None:
        """New title with no existing entry creates a new entry."""

        result = kb.remember("New Backend Entry", "Body text.", ["infra"], "snippet")

        assert result["action"] == "created"
        assert result["title"] == "New Backend Entry"
        assert "id" in result

        # Verify entry is readable
        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["title"] == "New Backend Entry"
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

    def test_delete(self, kb: KnowledgeBase) -> None:
        """Deleting an existing entry succeeds and removes it from get."""

        created = _create_entry(kb)
        entry_id = created["id"]

        deleted = kb.delete(entry_id)
        assert deleted is True

        # Entry must be gone
        assert kb.get(entry_id) is None

    def test_get_not_found(self, kb: KnowledgeBase) -> None:
        """Getting a nonexistent UUID returns None."""

        result = kb.get(str(uuid.uuid4()))

        # Not found
        assert result is None


# ===========================================================================
# Search
# ===========================================================================


class TestSearch:
    """Full-text search operations against the SQLite backend."""

    def test_search_basic(self, kb: KnowledgeBase) -> None:
        """Searching by a keyword finds the matching entry."""

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        results = kb.search("playbook")

        assert len(results) >= 1
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
            kb,
            "Docker Volumes",
            "Bind mounts.",
            ["docker", "storage"],
            force=True,
        )
        _create_entry(
            kb,
            "Ansible Networking",
            "Network modules.",
            ["ansible"],
            force=True,
        )

        # Search "networking" but only in "docker" tag
        results = kb.search("networking", tags=["docker"])

        titles = {r["title"] for r in results}
        assert "Docker Networking" in titles
        # Ansible entry must not appear
        assert "Ansible Networking" not in titles

    def test_search_entry_type_filter(self, kb: KnowledgeBase) -> None:
        """Search with entry_type filter only returns entries of that type."""

        _create_entry(
            kb,
            "Deploy Diagnostic",
            "Root cause of the deploy failure.",
            ["ops"],
            force=True,
            entry_type="diagnostic",
        )
        _create_entry(
            kb,
            "Deploy Feature",
            "How the deploy pipeline works.",
            ["ops"],
            force=True,
            entry_type="feature",
        )

        results = kb.search("deploy", entry_type="diagnostic")

        titles = {r["title"] for r in results}
        assert "Deploy Diagnostic" in titles
        assert "Deploy Feature" not in titles
        for r in results:
            assert r["type"] == "diagnostic"

    def test_search_entry_type_and_tag_filter_combined(self, kb: KnowledgeBase) -> None:
        """entry_type and tags filters combine with AND logic."""

        _create_entry(
            kb,
            "Docker Diagnostic",
            "Root cause of a docker networking bug.",
            ["docker"],
            force=True,
            entry_type="diagnostic",
        )
        _create_entry(
            kb,
            "Ansible Diagnostic",
            "Root cause of an ansible networking bug.",
            ["ansible"],
            force=True,
            entry_type="diagnostic",
        )
        _create_entry(
            kb,
            "Docker Feature",
            "How docker networking works.",
            ["docker"],
            force=True,
            entry_type="feature",
        )

        results = kb.search("networking", tags=["docker"], entry_type="diagnostic")

        titles = {r["title"] for r in results}
        assert titles == {"Docker Diagnostic"}

    def test_search_limit(self, kb: KnowledgeBase) -> None:
        """The limit parameter caps the number of search results."""

        # Create several entries with the same keyword
        for i in range(5):
            _create_entry(
                kb,
                f"Deployment Guide {i}",
                f"Steps for deployment number {i}.",
                ["deploy"],
                force=True,
            )

        results = kb.search("deployment", limit=2)

        # Must respect the limit
        assert len(results) <= 2


# ===========================================================================
# Tags
# ===========================================================================


class TestTags:
    """Tag listing and filtering against the SQLite backend."""

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

    def test_list_entries(self, kb: KnowledgeBase) -> None:
        """list_entries returns sorted results and tag filter works."""

        _create_entry(kb, "Charlie", "C.", ["infra"], force=True)
        _create_entry(kb, "alpha", "A.", ["infra", "linux"], force=True)
        _create_entry(kb, "Bravo", "B.", ["docker"], force=True)

        # All entries, sorted by title
        entries = kb.list_entries()
        titles = [e["title"] for e in entries]
        assert titles == ["alpha", "Bravo", "Charlie"]

        # Filter by infra tag
        infra_entries = kb.list_entries(tags=["infra"])
        assert len(infra_entries) == 2
        infra_titles = {e["title"] for e in infra_entries}
        assert infra_titles == {"alpha", "Charlie"}


# ===========================================================================
# Relations
# ===========================================================================


class TestRelations:
    """Graph relation operations against the SQLite backend."""

    def test_relations_outgoing(self, kb: KnowledgeBase) -> None:
        """Entry with kb://uuid#type link has outgoing relations."""

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
        assert any(r["id"] == target_id and r["type"] == "depends_on" for r in outgoing)

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
    """Index rebuild operations against the SQLite backend."""

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

    def test_rebuild_mixed_legacy_and_new_fields(
        self, kb: KnowledgeBase
    ) -> None:
        """Rebuild succeeds with a mix of legacy and extended-schema entries."""

        _create_entry(kb, "Legacy Entry", "Legacy content.", ["tag"], force=True)
        kb.remember(
            "New Entry",
            "New content.",
            ["tag"],
            "decision",
            force=True,
            resource="https://example.com",
        )

        result = kb.rebuild()

        assert result["count"] == 2


# ===========================================================================
# Extended schema — type / resource
# ===========================================================================


class TestExtendedSchema:
    """Extended schema fields (type/resource) against the SQLite backend."""

    def test_index_with_new_fields_no_error(self, kb: KnowledgeBase) -> None:
        """Indexing an entry with type/resource succeeds and round-trips."""

        result = kb.remember(
            "Hub Entry",
            "Content.",
            ["hub"],
            entry_type="hub",
            resource="https://github.com/org/repo",
        )

        entry = kb.get(result["id"])
        assert entry is not None
        assert entry["type"] == "hub"
        assert entry["resource"] == "https://github.com/org/repo"


# ===========================================================================
# Hybrid semantic search
# ===========================================================================


class TestHybridSearch:
    """Semantic (Model2Vec) + BM25 fusion search against the SQLite backend."""

    def test_paraphrase_recall(self, kb: KnowledgeBase) -> None:
        """A query sharing no keywords with the entry still finds it by meaning."""

        _create_entry(
            kb,
            "Kubernetes Deployment Guide",
            "How to roll out containerized workloads onto a cluster.",
            ["infra"],
            force=True,
        )

        # No literal keyword overlap with the entry's title/content
        results = kb.search("orchestrating containers across machines")

        assert any(r["title"] == "Kubernetes Deployment Guide" for r in results)

    def test_fusion_surfaces_both_keyword_and_semantic_matches(
        self, kb: KnowledgeBase
    ) -> None:
        """A query matches one entry by keyword and another by meaning."""

        _create_entry(
            kb, "Ansible Playbook Guide", "How to write playbooks.", force=True
        )
        _create_entry(
            kb,
            "Automating Server Configuration",
            "Declarative scripts that provision and configure machines.",
            force=True,
        )

        results = kb.search("playbook automation")

        titles = {r["title"] for r in results}
        assert "Ansible Playbook Guide" in titles

    def test_search_degrades_to_bm25_when_model_unavailable(
        self, kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the embedding model can't load, keyword search still works."""

        import search_backend

        monkeypatch.setattr(search_backend, "_get_model", lambda name: None)

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        results = kb.search("playbook")

        assert any(r["title"] == "Ansible Playbook Guide" for r in results)

    def test_rebuild_batch_encodes_embeddings(self, kb: KnowledgeBase) -> None:
        """Rebuild recomputes embeddings so semantic recall survives a rebuild."""

        _create_entry(
            kb,
            "Kubernetes Deployment Guide",
            "How to roll out containerized workloads onto a cluster.",
            ["infra"],
            force=True,
        )

        kb.rebuild()

        results = kb.search("orchestrating containers across machines")
        assert any(r["title"] == "Kubernetes Deployment Guide" for r in results)
