"""
SQLite backend tests for Engram.

Exercises the KnowledgeBase CRUD, search, tags, relations, and rebuild
operations against the SQLite FTS5 backend.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import KnowledgeBase
from schema import parse_schema
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


# Well-formed hub UUID for membership-required creates — target existence
# is doctor's concern, only the format is enforced at write time
_HUB_ID = "11111111-1111-4111-8111-111111111111"

# Types whose creation requires part_of (schema membership: required)
_MEMBERSHIP_REQUIRED = {
    "decision", "diagnostic", "feature", "procedure", "integration"
}


def _create_entry(
    kb: KnowledgeBase,
    title: str = "Test Entry",
    content: str = "Some content for testing.",
    tags: list[str] | None = None,
    *,
    force: bool = False,
    entry_type: str = "snippet",
    part_of: list[str] | None = None,
) -> dict[str, Any]:
    """Shorthand for creating an entry and asserting success."""

    if tags is None:
        tags = ["test"]
    if part_of is None and entry_type in _MEMBERSHIP_REQUIRED:
        part_of = [_HUB_ID]

    result = kb.remember(
        title, content, tags, entry_type, force=force, part_of=part_of
    )
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


class TestExactMatchChannel:
    """The literal title/tag overlap channel fused into search()."""

    def test_exact_match_search_ranks_by_token_overlap(
        self, kb: KnowledgeBase
    ) -> None:
        """More matching tokens in title/tags ranks an entry higher."""

        two_token = _create_entry(
            kb, "Ledgerbird invoice numbering", "Body one.", ["ledgerbird"]
        )
        one_token = _create_entry(
            kb, "Ledgerbird overview", "Body two.", ["misc"], force=True
        )
        _create_entry(kb, "Unrelated entry", "Body three.", ["misc"], force=True)

        conn = kb._backend._connect()
        try:
            ids = kb._backend._exact_match_search(
                conn, "Ledgerbird invoice numbering", None, 10
            )
        finally:
            conn.close()

        assert ids[0] == two_token["id"]
        assert one_token["id"] in ids
        assert len(ids) == 2

    def test_exact_match_search_skips_short_tokens(
        self, kb: KnowledgeBase
    ) -> None:
        """Tokens shorter than MIN_EXACT_TOKEN_LEN contribute no signal."""

        conn = kb._backend._connect()
        try:
            ids = kb._backend._exact_match_search(conn, "a to is", None, 10)
        finally:
            conn.close()

        # No tokens long enough to search on
        assert ids == []

    def test_exact_match_search_abstains_with_one_discriminating_token(
        self, kb: KnowledgeBase
    ) -> None:
        """A single literally-matching token ties every entry that has
        it and can't break the tie — the channel must contribute
        nothing rather than let SQL row order decide.
        """

        _create_entry(kb, "Ledgerbird overview", "Body one.", ["ledgerbird"])
        _create_entry(
            kb, "Ledgerbird billing notes", "Body two.", ["ledgerbird"], force=True
        )

        conn = kb._backend._connect()
        try:
            # "проверка" has no literal match anywhere — only "ledgerbird"
            # (one discriminating token) matches both entries' titles.
            ids = kb._backend._exact_match_search(
                conn, "Ledgerbird проверка", None, 10
            )
        finally:
            conn.close()

        assert ids == []

    def test_exact_match_channel_recovers_compound_title_miss(
        self, kb: KnowledgeBase
    ) -> None:
        """A query word inside a compound title token BM25 can't split.

        FTS5 tokenizes "ZephyrSprocket" as one token — a query for
        "zephyr" alone never matches it via BM25, no matter how the
        candidate pool is sized. Several distractors share generic
        vocabulary with the query at high term frequency, which is
        exactly the dilution failure mode the exact-match channel
        targets: literal substring containment in the title still
        surfaces the entry via RRF fusion even though BM25 misses it
        outright.
        """

        target = _create_entry(
            kb,
            "ZephyrSprocket rollout notes",
            "Canary checks gate every rolling deploy.",
            ["infra"],
        )
        for i in range(5):
            _create_entry(
                kb,
                f"Rollout deploy canary report {i}",
                "Rolling deploy canary checks rollout deploy canary.",
                ["infra"],
                force=True,
            )

        hits = kb.search("zephyr rollout canary", limit=10)

        assert any(hit["id"] == target["id"] for hit in hits)


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


class TestMultiHopRelations:
    """hops=2 traversal: same-direction BFS, dedup, via, hop2_total."""

    def test_default_hops_is_one(self, kb: KnowledgeBase) -> None:
        """Without hops, a 2-away entry is invisible (unchanged behavior)."""

        c = _create_entry(kb, "C", "Leaf.", force=True)
        b = _create_entry(kb, "B", f"Links to [c](kb://{c['id']}#related).", force=True)
        a = _create_entry(kb, "A", f"Links to [b](kb://{b['id']}#related).", force=True)

        out = kb.get_relations(a["id"])["out"]
        assert {r["id"] for r in out} == {b["id"]}
        assert all(r["hops"] == 1 for r in out)

    def test_hops_two_reaches_second_level_with_via(self, kb: KnowledgeBase) -> None:
        """A -> B -> C: hops=2 surfaces C tagged hops=2, via=[B]."""

        c = _create_entry(kb, "C", "Leaf.", force=True)
        b = _create_entry(kb, "B", f"Links to [c](kb://{c['id']}#related).", force=True)
        a = _create_entry(kb, "A", f"Links to [b](kb://{b['id']}#related).", force=True)

        out = kb.get_relations(a["id"], hops=2)["out"]
        by_id = {r["id"]: r for r in out}

        assert by_id[b["id"]]["hops"] == 1
        assert "via" not in by_id[b["id"]]
        assert by_id[b["id"]]["title"] == "B"
        assert by_id[c["id"]]["hops"] == 2
        assert by_id[c["id"]]["via"] == [b["id"]]
        # Hop-2 items are navigation breadcrumbs, not read content — no
        # title resolution; recall(id) directly for that.
        assert "title" not in by_id[c["id"]]

    def test_hops_two_dedupes_convergent_paths(self, kb: KnowledgeBase) -> None:
        """B and E both link to C: C appears once, via lists both parents."""

        c = _create_entry(kb, "C", "Leaf.", force=True)
        b = _create_entry(kb, "B", f"Links to [c](kb://{c['id']}#related).", force=True)
        e = _create_entry(kb, "E", f"Links to [c](kb://{c['id']}#related).", force=True)
        a = _create_entry(
            kb,
            "A",
            f"Links to [b](kb://{b['id']}#related) and [e](kb://{e['id']}#related).",
            force=True,
        )

        out = kb.get_relations(a["id"], hops=2)["out"]
        c_rows = [r for r in out if r["id"] == c["id"]]

        assert len(c_rows) == 1
        assert c_rows[0]["hops"] == 2
        assert set(c_rows[0]["via"]) == {b["id"], e["id"]}

    def test_hops_two_never_revisits_the_root(self, kb: KnowledgeBase) -> None:
        """A -> B -> A (cycle): the root never re-enters its own hop-2 list."""

        a_id = _create_entry(kb, "A", "Placeholder, rewritten below.", force=True)["id"]
        b = _create_entry(kb, "B", f"Links back to [a](kb://{a_id}#related).", force=True)
        # Rewrite A's content now that B's id is known, closing the cycle.
        assert "error" not in kb.remember(
            "A", f"Links to [b](kb://{b['id']}#related).", ["test"], "snippet",
            force=True, entry_id=a_id,
        )

        out = kb.get_relations(a_id, hops=2)["out"]
        assert a_id not in {r["id"] for r in out}

    def test_hops_clamped_to_two(self, kb: KnowledgeBase) -> None:
        """hops above 2 behaves identically to hops=2 (hard cap)."""

        c = _create_entry(kb, "C", "Leaf.", force=True)
        b = _create_entry(kb, "B", f"Links to [c](kb://{c['id']}#related).", force=True)
        a = _create_entry(kb, "A", f"Links to [b](kb://{b['id']}#related).", force=True)

        assert kb.get_relations(a["id"], hops=5) == kb.get_relations(a["id"], hops=2)

    def test_hop2_total_and_truncation_flag(self, kb: KnowledgeBase) -> None:
        """hop2_total counts unique hop-2 nodes; hop2_truncated reflects the cap."""

        leaves = [_create_entry(kb, f"Leaf{i}", "Leaf.", force=True) for i in range(3)]
        b = _create_entry(
            kb,
            "B",
            "".join(f"[l{i}](kb://{leaf['id']}#related) " for i, leaf in enumerate(leaves)),
            force=True,
        )
        a = _create_entry(kb, "A", f"Links to [b](kb://{b['id']}#related).", force=True)

        relations = kb.get_relations(a["id"], limit=2, hops=2)
        assert relations["hop2_total"] == 2  # B's own neighbor query was capped at 2
        assert relations["hop2_truncated"] is True

        relations_uncapped = kb.get_relations(a["id"], hops=2)
        assert relations_uncapped["hop2_total"] == 3
        assert relations_uncapped["hop2_truncated"] is False

    def test_in_digest_excludes_hop2_arrivals(self, kb: KnowledgeBase) -> None:
        """Hub's in_digest counts only direct back-links, even at hops=2."""

        hub = kb.remember(
            "Hub", "A project hub.", ["test"], "hub", resource="/tmp/hub", force=True
        )
        hub_id = hub["id"]
        # Y belongs to an unrelated hub — its only connection to `hub` is
        # the kb:// chain via X, so part_of must not smuggle it into the
        # digest through the structural-membership channel.
        other_hub_id = kb.remember(
            "Other Hub", "Unrelated project.", ["test"], "hub",
            resource="/tmp/other-hub", force=True,
        )["id"]

        # X links directly to hub (hop-1 backlink); Y links to X, not to hub.
        x = _create_entry(
            kb, "X", f"Refers to [hub](kb://{hub_id}#related).",
            entry_type="decision", part_of=[hub_id], force=True,
        )
        _create_entry(
            kb, "Y", f"Contradicts [x](kb://{x['id']}#related:contradicts).",
            entry_type="diagnostic", part_of=[other_hub_id], force=True,
        )

        entry = kb.get(hub_id, with_relations=True, digest=True, hops=2)
        assert entry is not None
        digest = entry["relations"]["in_digest"]

        # Y is 2 hops from the hub (via X) but never links to the hub
        # directly — in_digest must not count it.
        decision_sample_ids = {m["id"] for m in digest.get("decision", {}).get("sample", [])}
        assert x["id"] in decision_sample_ids
        diagnostic_bucket = digest.get("diagnostic", {"count": 0})
        assert diagnostic_bucket["count"] == 0


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
            part_of=[_HUB_ID],
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

    def test_bm25_or_fallback_on_partial_keyword_match(
        self, kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A query with one term absent from the KB still gets keyword hits
        (implicit-AND FTS5 would return nothing without the OR retry)."""

        import search_backend

        monkeypatch.setattr(search_backend, "_get_model", lambda name: None)

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        results = kb.search("playbook zzyzxq nonexistentterm")

        assert any(r["title"] == "Ansible Playbook Guide" for r in results)

    def test_bm25_all_garbage_query_stays_empty(
        self, kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The OR fallback must not fabricate matches for pure noise."""

        import search_backend

        monkeypatch.setattr(search_backend, "_get_model", lambda name: None)

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        results = kb.search("zzyzxq nonexistentterm")

        assert results == []

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


# ===========================================================================
# FTS5 index integrity (external-content table)
# ===========================================================================


class TestFTSIndexIntegrity:
    """
    Regression tests for the contentless-FTS5 bug.

    entries_fts used to be a genuinely contentless table (content=''),
    which silently orphaned a duplicate row on every update (INSERT OR
    REPLACE on the TEXT-keyed entries table reassigns a new rowid, so the
    "delete old FTS row" step targeted a stale rowid and matched nothing)
    and made unindex() raise "cannot DELETE from contentless fts5 table"
    once a row's rowid finally did match real indexed content.
    """

    def test_update_does_not_orphan_fts_row(self, kb: KnowledgeBase) -> None:
        """Updating an entry keeps entries and entries_fts row counts equal."""

        created = _create_entry(kb, "Original Title", "Original body.")
        kb.remember(
            "Updated Title",
            "Updated body, different content entirely.",
            ["updated"],
            "snippet",
            entry_id=created["id"],
        )

        conn = kb._backend._connect()
        try:
            entries_count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            fts_count = conn.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0]
        finally:
            conn.close()

        assert entries_count == fts_count == 1

    def test_unindex_does_not_raise_on_indexed_entry(self, kb: KnowledgeBase) -> None:
        """unindex() on an entry with real indexed content must not raise."""

        created = _create_entry(kb, "To Delete", "Content that gets indexed.")

        # Must not raise sqlite3.OperationalError
        kb._backend.unindex(created["id"])

        conn = kb._backend._connect()
        try:
            entries_count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        finally:
            conn.close()

        assert entries_count == 0

    def test_delete_reports_success_and_leaves_no_orphan(self, kb: KnowledgeBase) -> None:
        """delete() actually removes the index row, not just the Markdown file."""

        created = _create_entry(kb, "To Delete", "Content that gets indexed.")

        assert kb.delete(created["id"]) is True

        conn = kb._backend._connect()
        try:
            row = conn.execute(
                "SELECT id FROM entries WHERE id = ?", (created["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert row is None


# ===========================================================================
# Entity-resolution auto-linking
# ===========================================================================


class TestAutoLinking:
    """Suggested kb:// links surfaced by remember() via embedding similarity."""

    def test_suggests_similar_entry(self, kb: KnowledgeBase) -> None:
        """A near-duplicate-topic entry is suggested as a link candidate."""

        _create_entry(
            kb,
            "Hybrid search backend shipped (BM25 + embeddings via RRF)",
            "SQLiteBackend.search() fuses FTS5 BM25 keyword search with "
            "cosine similarity over local Model2Vec embeddings.",
            ["search"],
            force=True,
        )

        result = kb.remember(
            "Roadmap: entity resolution reuses the embedding search layer",
            "On remember, run new content through the existing embedding "
            "layer used by hybrid BM25 + Model2Vec semantic search to "
            "suggest kb:// links to similar entries.",
            ["search", "roadmap"],
            "idea",
            force=True,
        )

        assert "suggested_links" in result
        assert any(
            "Hybrid search backend" in link["title"]
            for link in result["suggested_links"]
        )

    def test_no_suggestions_for_unrelated_content(self, kb: KnowledgeBase) -> None:
        """An entry on an unrelated topic gets no suggested links."""

        _create_entry(
            kb,
            "Hybrid search backend shipped (BM25 + embeddings via RRF)",
            "SQLiteBackend.search() fuses FTS5 BM25 keyword search with "
            "cosine similarity over local Model2Vec embeddings.",
            ["search"],
            force=True,
        )

        result = kb.remember(
            "Grandmother's sourdough starter feeding schedule",
            "Feed the starter equal parts flour and water every morning, "
            "discard half before feeding, keep it at room temperature.",
            ["baking"],
            "snippet",
            force=True,
        )

        assert result.get("suggested_links", []) == []

    def test_does_not_suggest_already_linked_entry(self, kb: KnowledgeBase) -> None:
        """An entry already referenced via kb:// is excluded from suggestions."""

        target = _create_entry(
            kb,
            "Hybrid search backend shipped (BM25 + embeddings via RRF)",
            "SQLiteBackend.search() fuses FTS5 BM25 keyword search with "
            "cosine similarity over local Model2Vec embeddings.",
            ["search"],
            force=True,
        )

        result = kb.remember(
            "Roadmap: entity resolution reuses the embedding search layer",
            "On remember, run new content through the existing embedding "
            "layer used by hybrid BM25 + Model2Vec semantic search "
            f"(see [hybrid backend](kb://{target['id']}#feature)) to "
            "suggest kb:// links to similar entries.",
            ["search", "roadmap"],
            "idea",
            force=True,
        )

        linked_ids = {link["id"] for link in result.get("suggested_links", [])}
        assert target["id"] not in linked_ids


# ===========================================================================
# Bounded relations (recall relations_limit)
# ===========================================================================


class TestRelationLimits:
    """Per-direction relation caps and truncation reporting."""

    def _source_with_three_links(self, kb: KnowledgeBase) -> str:
        targets = [
            _create_entry(kb, f"Target {i}", f"Target body {i}.", force=True)["id"]
            for i in range(3)
        ]
        links = " ".join(f"[t{i}](kb://{tid}#snippet)" for i, tid in enumerate(targets))
        source = _create_entry(kb, "Source", f"Links: {links}", force=True)

        # Source created
        return source["id"]

    def test_totals_reported_without_limit(self, kb: KnowledgeBase) -> None:
        """Uncapped get_relations still reports out/in totals."""

        source_id = self._source_with_three_links(kb)

        relations = kb.get_relations(source_id)

        assert len(relations["out"]) == 3
        assert relations["out_total"] == 3

    def test_limit_truncates_and_flags(self, kb: KnowledgeBase) -> None:
        """relations_limit caps the lists and sets relations_truncated."""

        source_id = self._source_with_three_links(kb)

        entry = kb.get(source_id, with_relations=True, relations_limit=2)

        assert entry is not None
        assert len(entry["relations"]["out"]) == 2
        assert entry["relations"]["out_total"] == 3
        assert entry["relations_truncated"] is True

    def test_no_flag_when_under_limit(self, kb: KnowledgeBase) -> None:
        """No truncation flag when relations fit within the limit."""

        source_id = self._source_with_three_links(kb)

        entry = kb.get(source_id, with_relations=True, relations_limit=10)

        assert entry is not None
        assert "relations_truncated" not in entry


# ===========================================================================
# Usage-based staleness
# ===========================================================================


class TestUsageStaleness:
    """Access counters and lazy staleness scoring."""

    def test_search_hits_do_not_increment_access_count(
        self, kb: KnowledgeBase
    ) -> None:
        """Search hits are impressions, not reads — no counter bump
        (bumping them creates a rich-get-richer ranking feedback loop)."""

        _create_entry(kb, "Ansible Playbook Guide", "How to write playbooks.")

        first = kb.search("playbook")
        second = kb.search("playbook")

        assert first[0]["access_count"] == 0
        assert second[0]["access_count"] == 0
        assert "staleness" in second[0]

    def test_recall_increments_access_count(self, kb: KnowledgeBase) -> None:
        """get(record_access=True) bumps the counter; plain get does not."""

        created = _create_entry(kb, "Recalled Entry", "Body.")

        kb.get(created["id"])
        kb.get(created["id"], record_access=True)

        conn = kb._backend._connect()
        try:
            row = conn.execute(
                "SELECT access_count FROM entries WHERE id = ?", (created["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert row["access_count"] == 1

    def test_rebuild_preserves_access_counters(self, kb: KnowledgeBase) -> None:
        """Usage counters are index-only data but survive a rebuild."""

        created = _create_entry(kb, "Persistent Entry", "Body.")
        kb.get(created["id"], record_access=True)
        kb.get(created["id"], record_access=True)

        kb.rebuild()

        conn = kb._backend._connect()
        try:
            row = conn.execute(
                "SELECT access_count FROM entries WHERE id = ?", (created["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert row["access_count"] == 2

    def test_naive_last_accessed_does_not_break_search(
        self, kb: KnowledgeBase
    ) -> None:
        """A tz-naive timestamp in the index is read as UTC, not a crash."""

        created = _create_entry(kb, "Naive Timestamp", "Body about clocks.")

        conn = kb._backend._connect()
        try:
            conn.execute(
                "UPDATE entries SET access_count = 3, last_accessed = ? "
                "WHERE id = ?",
                ("2020-01-01T00:00:00", created["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        hits = {hit["id"]: hit for hit in kb.search("clocks")}

        assert created["id"] in hits
        # Naive timestamp parsed without raising; floored at 1.0 since
        # it was once accessed (see test_decay_is_monotonic_...)
        assert hits[created["id"]]["staleness"] == pytest.approx(1.0, abs=1e-3)

    def test_decay_is_monotonic_across_a_single_recall(
        self, kb: KnowledgeBase
    ) -> None:
        """An entry recalled once, long ago, must not decay below a
        never-recalled twin (TODO 14.1: previously it did, once decay ate
        through the frequency boost — escaping decay required a search
        hit plus a recall, which decay itself made progressively harder
        to earn back)."""

        recalled = _create_entry(kb, "Recalled Widget", "Widget notes A.")
        never = _create_entry(kb, "Untouched Widget", "Widget notes B.")

        kb.get(recalled["id"], record_access=True)

        conn = kb._backend._connect()
        try:
            conn.execute(
                "UPDATE entries SET last_accessed = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", recalled["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        by_id = {hit["id"]: hit for hit in kb.search("widget")}

        assert by_id[recalled["id"]]["staleness"] >= by_id[never["id"]]["staleness"]
        assert by_id[recalled["id"]]["staleness"] == pytest.approx(1.0, abs=1e-3)

    def test_never_accessed_entries_are_unaffected_by_age(
        self, kb: KnowledgeBase
    ) -> None:
        """A never-recalled entry keeps decay 1.0 no matter how old —
        the floor only kicks in once there's a read history to protect;
        stable, rarely-reread facts (most decisions/diagnostics) aren't
        punished just for being old (see the rejected valid_at-anchor
        variant of this fix: it dragged MRR 0.857 -> 0.137 on eval/ by
        decaying every unread entry by age)."""

        created = _create_entry(kb, "Aging Gadget", "Gadget notes.")

        hits = {hit["id"]: hit for hit in kb.search("gadget")}

        assert hits[created["id"]]["staleness"] == pytest.approx(1.0, abs=1e-3)

    def test_staleness_boost_does_not_leak_into_unrelated_queries(
        self, kb: KnowledgeBase
    ) -> None:
        """A heavily-boosted entry must not out-rank the genuinely
        relevant hit for a query it has no real relevance to.

        On a small KB, search()'s candidate pool used to be a flat
        max(limit*5, 50) regardless of corpus size — on a KB of a few
        hundred entries that's a third to a half of the whole thing, so
        virtually every entry becomes a staleness-rescoring candidate for
        every query, and a boosted-but-irrelevant entry can still get
        pulled into the results. candidate_limit now also scales down as
        total_entries // CANDIDATE_FRACTION_DIVISOR (found via eval/'s
        golden set, follow-up to TODO 14.1)."""

        for i in range(20):
            _create_entry(kb, f"Gadget Filler {i}", "Notes about gadgets and widgets.")
        boosted = _create_entry(kb, "Gadget Filler Hot", "Notes about gadgets and widgets.")
        target = _create_entry(
            kb, "Zephyrsprocket Mechanism", "How the zephyrsprocket assembly works."
        )

        conn = kb._backend._connect()
        try:
            conn.execute(
                "UPDATE entries SET access_count = 20, last_accessed = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), boosted["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        hits = kb.search("zephyrsprocket")

        assert hits
        assert hits[0]["id"] == target["id"]

    def test_no_boost_types_are_exempt(self, tmp_path: Path) -> None:
        """Types listed in no_boost_types keep boost 1.0 despite reads.

        Hubs are read at the start of every session, so their access
        count is a systematic ranking skew rather than a usage signal.
        """

        backend = SQLiteBackend(
            tmp_path / "index" / "engram.db", no_boost_types=frozenset({"hub"})
        )
        kb = KnowledgeBase(str(tmp_path), backend=backend)

        hub = _create_entry(
            kb, "Widget Hub", "Widget overview.", entry_type="hub", force=True
        )
        idea = _create_entry(
            kb, "Widget Idea", "Widget thought.", entry_type="idea", force=True
        )
        for _ in range(5):
            kb.get(hub["id"], record_access=True)
            kb.get(idea["id"], record_access=True)

        by_id = {hit["id"]: hit for hit in kb.search("widget")}

        assert by_id[hub["id"]]["staleness"] == pytest.approx(1.0, abs=1e-3)
        assert by_id[idea["id"]]["staleness"] > 1.0

    def test_exemption_holds_without_wiring_it_by_hand(self, tmp_path: Path) -> None:
        """The KnowledgeBase applies the schema's exemption to any backend.

        Ranking must not depend on whether the caller happened to pass
        no_boost_types when constructing the backend itself.
        """

        kb = KnowledgeBase(str(tmp_path))

        hub = _create_entry(
            kb, "Gadget Hub", "Gadget overview.", entry_type="hub", force=True
        )
        idea = _create_entry(
            kb, "Gadget Idea", "Gadget thought.", entry_type="idea", force=True
        )
        for _ in range(5):
            kb.get(hub["id"], record_access=True)
            kb.get(idea["id"], record_access=True)

        by_id = {hit["id"]: hit for hit in kb.search("gadget")}

        assert by_id[hub["id"]]["staleness"] == pytest.approx(1.0, abs=1e-3)
        assert by_id[idea["id"]]["staleness"] > 1.0

    def test_staleness_is_display_only_does_not_reorder_results(
        self, kb: KnowledgeBase
    ) -> None:
        """A heavy access_count is annotated on a result but never changes
        result order — an eval A/B (28 golden queries, eval/) showed
        folding it into ranking is net-harmful (MRR 0.857 unboosted vs
        0.702 with the old boost formula); staleness is now display-only.

        Both entries below match the query on keyword overlap ("gadget"),
        so the heavily-accessed one stays in the candidate pool — this
        checks it doesn't get promoted above the more relevant match by
        its access_count."""

        boosted = _create_entry(
            kb, "Gadget Notes", "General gadget and widget notes."
        )
        target = _create_entry(
            kb, "Gadget Zephyrsprocket Mechanism", "How the zephyrsprocket assembly works."
        )

        conn = kb._backend._connect()
        try:
            conn.execute(
                "UPDATE entries SET access_count = 20, last_accessed = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), boosted["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        hits = {hit["id"]: hit for hit in kb.search("gadget zephyrsprocket")}

        assert hits[boosted["id"]]["staleness"] > 1.0
        # Order unaffected by the boosted entry's inflated staleness
        assert hits[target["id"]]["score"] >= hits[boosted["id"]]["score"]

    def test_get_usage_snapshot(self, kb: KnowledgeBase) -> None:
        """Reads access_count/last_accessed for every indexed entry."""

        created = _create_entry(kb, "Snapshot Target", "Body.")
        kb.get(created["id"], record_access=True)
        kb.get(created["id"], record_access=True)

        snapshot = kb._backend.get_usage_snapshot()

        assert snapshot[created["id"]]["access_count"] == 2
        assert snapshot[created["id"]]["last_accessed"]


# ===========================================================================
# Relation index
# ===========================================================================


class TestRelationIndex:
    """The incoming-link index must exist on both schema paths."""

    def _has_index(self, kb: KnowledgeBase) -> bool:
        """Whether idx_relations_target is present in the database."""

        conn = kb._backend._connect()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_relations_target'"
            ).fetchone()
        finally:
            conn.close()

        return row is not None

    def test_index_created_on_init(self, kb: KnowledgeBase) -> None:
        """_ensure_schema creates the index."""

        assert self._has_index(kb)

    def test_index_survives_rebuild(self, kb: KnowledgeBase) -> None:
        """rebuild drops the relations table, so it must recreate the index."""

        _create_entry(kb, "Indexed Entry", "Body.", force=True)

        kb.rebuild()

        assert self._has_index(kb)


# ===========================================================================
# Content-addressed embedding cache
# ===========================================================================


class TestEmbeddingCache:
    """Rebuild reuses stored embeddings for unchanged entries."""

    def test_rebuild_reuses_embeddings_when_model_unavailable(
        self, kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cached embeddings survive a rebuild even with no model to re-encode."""

        import search_backend

        if kb._backend.embed("probe") is None:
            pytest.skip("Embedding model unavailable in this environment")

        _create_entry(kb, "Alpha Entry", "Alpha content.", force=True)
        _create_entry(kb, "Beta Entry", "Beta content.", force=True)

        # With the model gone, any surviving embedding must come from the cache
        monkeypatch.setattr(search_backend, "_get_model", lambda name: None)

        kb.rebuild()

        conn = kb._backend._connect()
        try:
            missing = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE embedding IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()

        assert missing == 0

    def test_update_reuses_embedding_for_unchanged_text(
        self, kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-indexing identical title+content skips the encoder."""

        import search_backend

        if kb._backend.embed("probe") is None:
            pytest.skip("Embedding model unavailable in this environment")

        created = _create_entry(kb, "Stable Entry", "Stable body.")

        monkeypatch.setattr(search_backend, "_get_model", lambda name: None)

        # Same text — the stored embedding must be reused, not dropped
        kb._backend.index(
            {
                "id": created["id"],
                "title": "Stable Entry",
                "tags": ["test"],
                "content": "Stable body.",
            }
        )

        conn = kb._backend._connect()
        try:
            row = conn.execute(
                "SELECT embedding FROM entries WHERE id = ?", (created["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert row["embedding"] is not None


# ===========================================================================
# Typed relation edges
# ===========================================================================


class TestTypedEdges:
    """kb://uuid#type:edge relationship semantics."""

    def test_edge_suffix_round_trips(self, kb: KnowledgeBase) -> None:
        """A :contradicts edge is stored and surfaced in both directions."""

        target = _create_entry(kb, "Claim", "The sky is green.", force=True)
        source = _create_entry(
            kb,
            "Counter-claim",
            f"Contradicts [claim](kb://{target['id']}#snippet:contradicts).",
            force=True,
        )

        out = kb.get_relations(source["id"])["out"]
        assert any(
            r["id"] == target["id"]
            and r["type"] == "snippet"
            and r["edge"] == "contradicts"
            for r in out
        )

        incoming = kb.get_relations(target["id"])["in"]
        assert any(
            r["id"] == source["id"] and r["edge"] == "contradicts" for r in incoming
        )

    def test_plain_link_defaults_to_related_to(self, kb: KnowledgeBase) -> None:
        """Links without :edge keep working with the default edge type."""

        target = _create_entry(kb, "Target", "Body.", force=True)
        source = _create_entry(
            kb,
            "Source",
            f"See [target](kb://{target['id']}#snippet).",
            force=True,
        )

        out = kb.get_relations(source["id"])["out"]
        assert any(
            r["id"] == target["id"] and r["edge"] == "related_to" for r in out
        )

    def test_schema_vocabulary_drives_extraction(self, tmp_path: Path) -> None:
        """The schema's `edges` list, not a hardcoded set, validates links."""

        schema = parse_schema(
            {
                "version": 1,
                "types": {"snippet": {}},
                "edges": ["refines", "related_to"],
            }
        )
        kb = KnowledgeBase(str(tmp_path), schema=schema)

        target = _create_entry(kb, "Target", "Body.", force=True)
        source = _create_entry(
            kb,
            "Source",
            f"A [refinement](kb://{target['id']}#snippet:refines) and a "
            f"[contradiction](kb://{target['id']}#snippet:contradicts).",
            force=True,
        )

        edges = {rel["edge"] for rel in kb.get_relations(source["id"])["out"]}

        # 'refines' is declared here; 'contradicts' no longer is
        assert edges == {"refines", "related_to"}

    def test_unknown_edge_falls_back_to_default(self) -> None:
        """An edge outside the vocabulary degrades to related_to."""

        from search_backend import extract_relations

        target_id = str(uuid.uuid4())
        relations = extract_relations(
            f"Ref [x](kb://{target_id}#snippet:blesses)."
        )

        assert relations == [
            {"target": target_id, "type": "snippet", "edge": "related_to"}
        ]


# ===========================================================================
# Structural membership — part_of in search
# ===========================================================================


class TestPartOfSearch:
    """part_of as a search filter and hub titles as a keyword signal."""

    def test_search_part_of_filter(self, kb: KnowledgeBase) -> None:
        """Search narrows to members of the given hub."""

        hub_a = _create_entry(
            kb, "Alpha Hub", "Overview alpha.", force=True, entry_type="hub"
        )
        hub_b = _create_entry(
            kb, "Beta Hub", "Overview beta.", force=True, entry_type="hub"
        )
        _create_entry(
            kb,
            "Alpha Deploy",
            "Deploy pipeline notes.",
            force=True,
            entry_type="feature",
            part_of=[hub_a["id"]],
        )
        _create_entry(
            kb,
            "Beta Deploy",
            "Deploy pipeline notes as well.",
            force=True,
            entry_type="feature",
            part_of=[hub_b["id"]],
        )

        results = kb.search("deploy", part_of=[hub_a["id"]])

        titles = {r["title"] for r in results}
        assert "Alpha Deploy" in titles
        assert "Beta Deploy" not in titles

    def test_member_matches_hub_title_keywords(self, kb: KnowledgeBase) -> None:
        """The hub's title is indexed into its members' search text.

        With the project tag gone, this is what keeps a member entry
        findable by the project's name.
        """

        hub = _create_entry(
            kb, "Zephyrium Hub", "Overview.", force=True, entry_type="hub"
        )
        member = _create_entry(
            kb,
            "Cache Invalidation Fix",
            "Root cause of the cache bug.",
            force=True,
            entry_type="diagnostic",
            part_of=[hub["id"]],
        )

        results = kb.search("zephyrium")

        assert member["id"] in {r["id"] for r in results}

    def test_rebuild_preserves_hub_title_signal(self, kb: KnowledgeBase) -> None:
        """rebuild re-resolves hub titles into members' search text."""

        hub = _create_entry(
            kb, "Quixotic Hub", "Overview.", force=True, entry_type="hub"
        )
        member = _create_entry(
            kb,
            "Retry Logic Fix",
            "Root cause of the retry bug.",
            force=True,
            entry_type="diagnostic",
            part_of=[hub["id"]],
        )

        kb.rebuild()
        results = kb.search("quixotic")

        assert member["id"] in {r["id"] for r in results}


class TestQueryLog:
    """query_log SQLite writes and get_analytics_snapshot aggregation."""

    def test_log_query_event_persists_a_row(self, kb: KnowledgeBase) -> None:
        """A logged event is readable back from the query_log table."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00",
            session_id="sess-1",
            tool="search",
            query_text="widget",
            entry_type=None,
            returned_ids=["a", "b"],
            top_result_id="a",
            hit=True,
            latency_ms=12,
        )

        conn = kb._backend._connect()
        try:
            row = conn.execute("SELECT * FROM query_log").fetchone()
        finally:
            conn.close()

        assert row["tool"] == "search"
        assert row["session_id"] == "sess-1"
        assert row["hit"] == 1
        assert json.loads(row["returned_ids"]) == ["a", "b"]

    def test_query_text_truncated(self, kb: KnowledgeBase) -> None:
        """query_text longer than QUERY_LOG_TEXT_TRUNCATE is cut down."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00",
            session_id="sess-1",
            tool="search",
            query_text="x" * 1000,
            hit=False,
        )

        conn = kb._backend._connect()
        try:
            row = conn.execute("SELECT query_text FROM query_log").fetchone()
        finally:
            conn.close()

        from config import QUERY_LOG_TEXT_TRUNCATE

        assert len(row["query_text"]) == QUERY_LOG_TEXT_TRUNCATE

    def test_read_write_ratio(self, kb: KnowledgeBase) -> None:
        """reads (search+recall) / remembers, per logged events."""

        events = [
            ("search", None), ("search", None), ("recall", None), ("remember", None),
        ]
        for tool, _ in events:
            kb._backend.log_query_event(
                ts="2026-08-05T00:00:00+00:00", session_id="s", tool=tool, hit=True,
            )

        snapshot = kb.get_analytics_snapshot()

        assert snapshot["searches"] == 2
        assert snapshot["recalls"] == 1
        assert snapshot["remembers"] == 1
        assert snapshot["read_write_ratio"] == 3.0

    def test_read_write_ratio_none_without_writes(self, kb: KnowledgeBase) -> None:
        """No remember calls logged yet -> ratio is undefined, not a ZeroDivisionError."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search", hit=True,
        )

        assert kb.get_analytics_snapshot()["read_write_ratio"] is None

    def test_zero_hit_queries_and_hit_rate(self, kb: KnowledgeBase) -> None:
        """Zero-hit queries are listed verbatim; hit_rate averages the flag."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            query_text="found it", hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            query_text="nothing here", hit=False,
        )

        snapshot = kb.get_analytics_snapshot()

        assert snapshot["hit_rate"] == 0.5
        assert snapshot["zero_hit_queries"] == [{"query": "nothing here", "count": 1}]

    def test_hit_distribution_by_type(self, kb: KnowledgeBase) -> None:
        """Per-entry_type hit rate, from the entry_type filter used on search."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            entry_type="diagnostic", hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            entry_type="diagnostic", hit=False,
        )

        dist = kb.get_analytics_snapshot()["hit_distribution_by_type"]

        assert dist["diagnostic"] == {"hit_rate": 0.5, "total": 2}

    def test_dead_entries_never_accessed(self, kb: KnowledgeBase) -> None:
        """An entry never recalled counts as never_accessed."""

        _create_entry(kb, "Untouched", "Nobody reads this.")

        dead = kb.get_analytics_snapshot()["dead_entries"]

        assert dead["never_accessed"] == 1
        assert dead["total_entries"] == 1

    def test_click_through_and_recall_rank(self, kb: KnowledgeBase) -> None:
        """A recall of a search's top result, same session, is a click-through
        with recall rank 1; searches_per_recall counts the one search first."""

        created = _create_entry(kb, "Ranked Entry", "Unique ranked content.")

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            query_text="ranked", returned_ids=[created["id"]],
            top_result_id=created["id"], hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:01+00:00", session_id="s", tool="recall",
            entry_id=created["id"], hit=True,
        )

        snapshot = kb.get_analytics_snapshot()

        assert snapshot["click_through_rate"] == 1.0
        assert snapshot["average_recall_rank"] == 1
        assert snapshot["searches_per_recall"] == 1

    def test_click_through_outside_window_does_not_count(
        self, kb: KnowledgeBase
    ) -> None:
        """A recall long after the search still confirms recall rank, but
        is not a click-through past the configured window."""

        created = _create_entry(kb, "Late Entry", "Unique late content.")

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            query_text="late", returned_ids=[created["id"]],
            top_result_id=created["id"], hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T01:00:00+00:00", session_id="s", tool="recall",
            entry_id=created["id"], hit=True,
        )

        snapshot = kb.get_analytics_snapshot(click_through_window_minutes=30)

        assert snapshot["click_through_rate"] == 0.0
        assert snapshot["average_recall_rank"] == 1

    def test_recall_rank_beyond_top_result(self, kb: KnowledgeBase) -> None:
        """A recall of the 2nd result is not click-through, but rank is 2."""

        created = _create_entry(kb, "Second Place", "Unique second content.")

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s", tool="search",
            query_text="second", returned_ids=["other-id", created["id"]],
            top_result_id="other-id", hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:01+00:00", session_id="s", tool="recall",
            entry_id=created["id"], hit=True,
        )

        snapshot = kb.get_analytics_snapshot()

        assert snapshot["average_recall_rank"] == 2
        assert snapshot["click_through_rate"] == 0.0

    def test_sessions_touching_engram(self, kb: KnowledgeBase) -> None:
        """Distinct session_id count, across any tool."""

        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s1", tool="search", hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s2", tool="search", hit=True,
        )
        kb._backend.log_query_event(
            ts="2026-08-05T00:00:00+00:00", session_id="s1", tool="recall", hit=True,
        )

        assert kb.get_analytics_snapshot()["sessions_touching_engram"] == 2
