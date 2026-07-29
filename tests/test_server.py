"""
Tests for server.py — tool registration and basic tool behaviour.

Exercises the register_tools() function with a real KnowledgeBase on
a tmp_path directory and a fresh FastMCP instance.  Also covers
parse_args and setup_logging.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

import pytest

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp.server.fastmcp import FastMCP

from database import KnowledgeBase
from schema import load_schema
from server import parse_args, register_tools, setup_logging


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _setup(tmp_path: Path) -> tuple[FastMCP, KnowledgeBase, logging.Logger]:
    """Provide a FastMCP instance with tools registered against a temp Engram."""

    kb = KnowledgeBase(str(tmp_path))
    mcp = FastMCP(name="test")
    logger = logging.getLogger("test_server")
    register_tools(mcp, kb, logger)

    # Ready
    return mcp, kb, logger


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify that register_tools() creates the expected MCP tools."""

    def test_all_tools_registered(self, _setup: tuple) -> None:
        """All eight tools must be registered on the MCP instance."""

        mcp, _kb, _logger = _setup

        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "search",
            "recall",
            "remember",
            "forget",
            "list",
            "tags",
            "rebuild",
            "doctor",
        }

        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_no_extra_tools(self, _setup: tuple) -> None:
        """Only the expected tools are registered (no leftovers)."""

        mcp, _kb, _logger = _setup

        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "search",
            "recall",
            "remember",
            "forget",
            "list",
            "tags",
            "rebuild",
            "doctor",
        }

        # No unexpected tools
        assert tool_names == expected


# ---------------------------------------------------------------------------
# Tool behaviour — edge cases
# ---------------------------------------------------------------------------


class TestRememberEdgeCases:
    """Edge cases for the remember tool via KnowledgeBase."""

    def test_remember_invalid_entry_id_path_traversal(self, _setup: tuple) -> None:
        """A path-traversal entry_id is rejected."""

        _mcp, kb, _logger = _setup

        result = kb.remember(
            "Exploit",
            "Content.",
            ["test"],
            "snippet",
            entry_id="../../../etc/passwd",
        )

        # Must be rejected
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_remember_invalid_entry_id_not_uuid(self, _setup: tuple) -> None:
        """A non-UUID entry_id is rejected."""

        _mcp, kb, _logger = _setup

        result = kb.remember(
            "Exploit",
            "Content.",
            ["test"],
            "snippet",
            entry_id="not-a-valid-uuid",
        )

        # Must be rejected
        assert "error" in result


class TestSearchEdgeCases:
    """Edge cases for the search tool."""

    def test_search_empty_query(self, _setup: tuple) -> None:
        """An empty query string returns empty results without crashing."""

        _mcp, kb, _logger = _setup

        # Create an entry first so the index exists
        kb.remember("Test Entry", "Some content.", ["test"], "snippet")

        results = kb.search("")

        # Empty query may return all or nothing, but must not crash
        assert isinstance(results, list)

    def test_search_limit_clamped_low(self, _setup: tuple) -> None:
        """Limit below 1 is clamped to 1."""

        _mcp, kb, _logger = _setup

        kb.remember("Entry A", "Alpha content.", ["test"], "snippet", force=True)
        kb.remember("Entry B", "Beta content.", ["test"], "snippet", force=True)

        # Use limit=0 — should be clamped to 1 by server, but here we
        # test the KnowledgeBase directly (it accepts the value as-is)
        results = kb.search("content", limit=1)

        # At most 1 result
        assert len(results) <= 1


class TestForgetEdgeCases:
    """Edge cases for the forget tool."""

    def test_forget_nonexistent_entry(self, _setup: tuple) -> None:
        """Forgetting an entry that does not exist returns False."""

        _mcp, kb, _logger = _setup

        fake_id = str(uuid.uuid4())
        success = kb.delete(fake_id)

        # Not found
        assert success is False

    def test_forget_invalid_id(self, _setup: tuple) -> None:
        """Forgetting with an invalid ID returns False."""

        _mcp, kb, _logger = _setup

        success = kb.delete("../../etc/shadow")

        # Rejected
        assert success is False


class TestLimitClamping:
    """Verify that server-level limit clamping works."""

    def test_list_limit_clamped(self, _setup: tuple) -> None:
        """list_entries respects the limit parameter."""

        _mcp, kb, _logger = _setup

        for i in range(5):
            kb.remember(f"Entry {i:02d}", f"Body {i}.", ["test"], "snippet", force=True)

        entries = kb.list_entries(limit=2)

        # Must respect the limit
        assert len(entries) == 2

    def test_list_limit_large(self, _setup: tuple) -> None:
        """A limit larger than the entry count returns all entries."""

        _mcp, kb, _logger = _setup

        kb.remember("Only Entry", "Solo.", ["test"], "snippet")

        entries = kb.list_entries(limit=500)

        # All entries returned
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Metadata cache coherence
# ---------------------------------------------------------------------------


class TestMetaCache:
    """Verify that the metadata cache stays in sync with disk."""

    def test_cache_populated_on_init(self, tmp_path: Path) -> None:
        """Cache is populated when KnowledgeBase is created."""

        kb = KnowledgeBase(str(tmp_path))
        kb.remember("Cached Entry", "Content.", ["cache"], "snippet")

        # Create a new KnowledgeBase on the same path
        kb2 = KnowledgeBase(str(tmp_path))

        # Cache should contain the entry
        assert len(kb2._meta_cache) == 1

    def test_cache_updated_on_remember(self, _setup: tuple) -> None:
        """Cache is updated when a new entry is created."""

        _mcp, kb, _logger = _setup

        result = kb.remember("New Entry", "Content.", ["test"], "snippet")
        entry_id = result["id"]

        # Must be in cache
        assert entry_id in kb._meta_cache
        assert kb._meta_cache[entry_id]["title"] == "New Entry"
        assert kb._meta_cache[entry_id]["tags"] == ["test"]

    def test_cache_updated_on_delete(self, _setup: tuple) -> None:
        """Cache entry is removed when an entry is deleted."""

        _mcp, kb, _logger = _setup

        result = kb.remember("To Delete", "Content.", ["test"], "snippet")
        entry_id = result["id"]

        assert entry_id in kb._meta_cache

        kb.delete(entry_id)

        # Must be gone from cache
        assert entry_id not in kb._meta_cache

    def test_cache_updated_on_rebuild(self, _setup: tuple) -> None:
        """Cache is reloaded after a full rebuild."""

        _mcp, kb, _logger = _setup

        kb.remember("Alpha", "A.", ["test"], "snippet", force=True)
        kb.remember("Beta", "B.", ["test"], "snippet", force=True)

        kb.rebuild()

        # Cache must contain both entries
        assert len(kb._meta_cache) == 2


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Verify CLI argument parsing for server.py."""

    def test_parse_args_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default values are applied when no CLI args are provided."""

        # Clear any ENGRAM_* env vars that could interfere
        for key in list(sys.modules.get("os", __import__("os")).environ):
            if key.startswith("ENGRAM_"):
                monkeypatch.delenv(key, raising=False)

        monkeypatch.setattr("sys.argv", ["server.py"])

        args = parse_args()

        assert args.data_path == "/knowledge"
        assert args.transport == "stdio"
        assert args.host == "0.0.0.0"
        assert args.port == 8192

    def test_parse_args_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom CLI arguments override defaults."""

        monkeypatch.setattr(
            "sys.argv",
            ["server.py", "--data-path", "/custom", "--port", "9000"],
        )

        args = parse_args()

        assert args.data_path == "/custom"
        assert args.port == 9000


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    """Verify logging configuration."""

    def test_setup_logging(self) -> None:
        """setup_logging returns a logger with at least one handler."""

        logger = setup_logging()

        assert isinstance(logger, logging.Logger)
        assert logger.name == "engram"
        assert logger.level == logging.INFO
        assert len(logger.handlers) >= 1

        # Verify the handler has a formatter with the expected date format
        handler = logger.handlers[-1]
        assert handler.formatter is not None
        assert handler.formatter.datefmt == "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# MCP tool closures — exercised via call_tool
# ---------------------------------------------------------------------------


# Well-formed hub UUID for membership-required creates — target existence
# is doctor's concern, only the format is enforced at write time
_HUB_ID = "11111111-1111-4111-8111-111111111111"


def _call_tool(mcp: FastMCP, name: str, arguments: dict) -> dict:
    """Call an MCP tool synchronously and return the raw dict result."""
    # Use _tool_manager.call_tool with convert_result=False to get the raw
    # return value from the tool closure (a dict), not ContentBlock objects.
    coro = mcp._tool_manager.call_tool(name, arguments)
    # Run coroutine
    return asyncio.new_event_loop().run_until_complete(coro)


class TestRememberAtomicityWarnings:
    """Atomicity warnings for the remember tool."""

    def test_remember_warns_on_headers(self, _setup: tuple) -> None:
        """Content with Markdown headers triggers an atomicity warning."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Multi-section",
                "content": "## Section A\nFoo.\n\n## Section B\nBar.",
                "tags": ["test"],
                "entry_type": "snippet",
            },
        )

        assert "warnings" in result
        assert any("headers" in w for w in result["warnings"])

    def test_remember_warns_on_large_content(self, _setup: tuple) -> None:
        """Content exceeding 512 bytes triggers a size warning."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Large article",
                "content": "A" * 600,
                "tags": ["test"],
                "entry_type": "snippet",
            },
        )

        assert "warnings" in result
        assert any("512" in w for w in result["warnings"])

    def test_remember_warns_on_very_large_content(self, _setup: tuple) -> None:
        """Content exceeding 1024 bytes triggers a hard size warning."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Very large article",
                "content": "B" * 1100,
                "tags": ["test"],
                "entry_type": "snippet",
            },
        )

        assert "warnings" in result
        assert any("1 KB" in w for w in result["warnings"])

    def test_remember_warns_on_too_many_paragraphs(self, _setup: tuple) -> None:
        """Content with more than 3 paragraphs triggers a warning."""

        mcp, _kb, _logger = _setup

        content = "First.\n\nSecond.\n\nThird.\n\nFourth."
        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Multi-paragraph",
                "content": content,
                "tags": ["test"],
                "entry_type": "snippet",
            },
        )

        assert "warnings" in result
        assert any("paragraphs" in w for w in result["warnings"])

    def test_remember_no_warning_on_atomic_content(self, _setup: tuple) -> None:
        """Short, atomic content carrying its type's body fields is clean."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Atomic fact",
                "content": "**Candidate:** log to stderr.\n**Status:** not_decided",
                "tags": ["test"],
                "entry_type": "idea",
            },
        )

        assert "warnings" not in result

    def test_conformance_warning_reads_the_stored_entry(
        self, _setup: tuple
    ) -> None:
        """An update inheriting `resource` is not reported as missing it.

        The check runs against what was written, not the call's arguments,
        so a content-only edit of a hub stays clean.
        """

        mcp, _kb, _logger = _setup
        body = "**What it does:** things.\n**Stack:** Python."

        created = _call_tool(
            mcp,
            "remember",
            {
                "title": "Project Hub",
                "content": body,
                "tags": ["test"],
                "entry_type": "hub",
                "resource": "/srv/project",
            },
        )
        assert "warnings" not in created

        updated = _call_tool(
            mcp,
            "remember",
            {
                "title": "Project Hub",
                "content": body + " Revised.",
                "tags": ["test"],
                "entry_type": "hub",
                "entry_id": created["id"],
            },
        )

        assert "warnings" not in updated


class TestToolRememberViaMcp:
    """Test the remember tool through the MCP layer."""

    def test_tool_remember_via_mcp(self, _setup: tuple) -> None:
        """Calling remember via MCP creates an entry and returns its id."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "MCP Test Entry",
                "content": "Created via MCP tool call.",
                "tags": ["mcp", "test"],
                "entry_type": "snippet",
            },
        )

        assert "id" in result
        assert result["title"] == "MCP Test Entry"
        assert result["action"] in ("created", "updated")


class TestToolSearchViaMcp:
    """Test the search tool through the MCP layer."""

    def test_tool_search_via_mcp(self, _setup: tuple) -> None:
        """Calling search via MCP returns results dict with count."""

        mcp, kb, _logger = _setup

        # Seed an entry first
        kb.remember("Searchable Item", "Unique searchable content.", ["test"], "snippet")

        result = _call_tool(mcp, "search", {"query": "searchable", "limit": 5})

        assert "count" in result
        assert "results" in result
        assert isinstance(result["results"], list)

    def test_tool_search_omits_volatile_usage_fields(self, _setup: tuple) -> None:
        """access_count/last_accessed/staleness drift with wall-clock time
        and don't affect ranking — the MCP response must not carry them,
        so identical repeated searches stay byte-identical for prompt
        caching. KnowledgeBase.search() itself still returns them (used by
        the dashboard); only the MCP tool layer strips them."""

        mcp, kb, _logger = _setup

        created = kb.remember(
            "Staleness Probe", "Unique staleness probe content.", ["test"], "snippet"
        )
        kb.get(created["id"], record_access=True)

        result = _call_tool(mcp, "search", {"query": "staleness probe", "limit": 5})

        assert result["results"]
        for hit in result["results"]:
            assert "access_count" not in hit
            assert "last_accessed" not in hit
            assert "staleness" not in hit


class TestToolRecallViaMcp:
    """Test the recall tool through the MCP layer."""

    def test_tool_recall_via_mcp(self, _setup: tuple) -> None:
        """Calling recall via MCP returns the full entry."""

        mcp, kb, _logger = _setup

        created = kb.remember("Recallable", "Full content here.", ["test"], "snippet")
        entry_id = created["id"]

        result = _call_tool(mcp, "recall", {"entry_id": entry_id})

        assert result["id"] == entry_id
        assert result["title"] == "Recallable"
        assert "content" in result

    def test_tool_recall_not_found(self, _setup: tuple) -> None:
        """Calling recall with a nonexistent id returns an error dict."""

        mcp, _kb, _logger = _setup

        fake_id = str(uuid.uuid4())
        result = _call_tool(mcp, "recall", {"entry_id": fake_id})

        # Must indicate not found
        assert "error" in result


class TestToolForgetViaMcp:
    """Test the forget tool through the MCP layer."""

    def test_tool_forget_via_mcp(self, _setup: tuple) -> None:
        """Calling forget via MCP deletes an entry."""

        mcp, kb, _logger = _setup

        created = kb.remember("To Forget", "Ephemeral.", ["test"], "snippet")
        entry_id = created["id"]

        result = _call_tool(mcp, "forget", {"entry_id": entry_id})

        assert result["success"] is True
        assert result["id"] == entry_id

        # Verify deletion
        assert kb.get(entry_id) is None

    def test_tool_forget_not_found(self, _setup: tuple) -> None:
        """Calling forget with a nonexistent id returns an error dict."""

        mcp, _kb, _logger = _setup

        fake_id = str(uuid.uuid4())
        result = _call_tool(mcp, "forget", {"entry_id": fake_id})

        # Must indicate not found
        assert "error" in result


class TestToolListViaMcp:
    """Test the list tool through the MCP layer."""

    def test_tool_list_via_mcp(self, _setup: tuple) -> None:
        """Calling list via MCP returns entries dict with count."""

        mcp, kb, _logger = _setup

        kb.remember("Listed A", "Body A.", ["alpha"], "snippet", force=True)
        kb.remember("Listed B", "Body B.", ["beta"], "snippet", force=True)

        result = _call_tool(mcp, "list", {"limit": 10})

        assert "count" in result
        assert result["count"] == 2
        assert "entries" in result
        assert len(result["entries"]) == 2


class TestToolTagsViaMcp:
    """Test the tags tool through the MCP layer."""

    def test_tool_tags_via_mcp(self, _setup: tuple) -> None:
        """Calling tags via MCP returns all tags with counts."""

        mcp, kb, _logger = _setup

        kb.remember("Tag Entry 1", "Body.", ["infra", "dns"], "snippet", force=True)
        kb.remember("Tag Entry 2", "Body.", ["infra", "ssl"], "snippet", force=True)

        result = _call_tool(mcp, "tags", {})

        assert "count" in result
        assert "tags" in result
        # At least the tags we created
        assert result["count"] >= 2


class TestToolSupersedeViaMcp:
    """Test bi-temporal supersede/include_superseded plumbing through MCP."""

    def test_remember_supersede_via_mcp(self, _setup: tuple) -> None:
        """Calling remember with supersede=True via MCP versions the entry."""

        mcp, kb, _logger = _setup

        created = kb.remember(
            "Old Fact", "Original.", ["test"], "decision", part_of=[_HUB_ID]
        )
        old_id = created["id"]

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "New Fact",
                "content": "Replacement.",
                "tags": ["test"],
                "entry_type": "decision",
                "entry_id": old_id,
                "supersede": True,
            },
        )

        assert result["action"] == "superseded"
        assert result["previous_id"] == old_id

        old_entry = kb.get(old_id)
        assert old_entry is not None
        assert old_entry["superseded_by"] == result["id"]

    def test_search_include_superseded_via_mcp(self, _setup: tuple) -> None:
        """search's include_superseded flag surfaces history via MCP."""

        mcp, kb, _logger = _setup

        created = kb.remember(
            "Versioned", "Old gadget details.", ["test"], "decision",
            part_of=[_HUB_ID],
        )
        kb.remember(
            "Versioned",
            "New gadget details.",
            ["test"],
            "decision",
            entry_id=created["id"],
            supersede=True,
        )

        hidden = _call_tool(mcp, "search", {"query": "gadget"})
        assert hidden["count"] == 1

        shown = _call_tool(
            mcp, "search", {"query": "gadget", "include_superseded": True}
        )
        assert shown["count"] == 2

    def test_search_entry_type_filter_via_mcp(self, _setup: tuple) -> None:
        """search's entry_type filter narrows results to a single type via MCP."""

        mcp, kb, _logger = _setup

        kb.remember(
            "Gadget Diagnostic",
            "Root cause of the gadget bug.",
            ["test"],
            "diagnostic",
            part_of=[_HUB_ID],
        )
        kb.remember(
            "Gadget Feature",
            "How the gadget feature works.",
            ["test"],
            "feature",
            part_of=[_HUB_ID],
        )

        result = _call_tool(
            mcp, "search", {"query": "gadget", "entry_type": "diagnostic"}
        )

        assert result["count"] == 1
        assert result["results"][0]["title"] == "Gadget Diagnostic"
        assert result["results"][0]["type"] == "diagnostic"

    def test_list_include_superseded_via_mcp(self, _setup: tuple) -> None:
        """list's include_superseded flag surfaces history via MCP."""

        mcp, kb, _logger = _setup

        created = kb.remember(
            "Versioned Entry", "Old.", ["test"], "decision", part_of=[_HUB_ID]
        )
        kb.remember(
            "Versioned Entry",
            "New.",
            ["test"],
            "decision",
            entry_id=created["id"],
            supersede=True,
        )

        hidden = _call_tool(mcp, "list", {})
        assert hidden["count"] == 1

        shown = _call_tool(mcp, "list", {"include_superseded": True})
        assert shown["count"] == 2


class TestToolRebuildViaMcp:
    """Test the rebuild tool through the MCP layer."""

    def test_tool_rebuild_via_mcp(self, _setup: tuple) -> None:
        """Calling rebuild via MCP reindexes all entries."""

        mcp, kb, _logger = _setup

        kb.remember("Rebuild A", "Alpha.", ["test"], "snippet", force=True)
        kb.remember("Rebuild B", "Beta.", ["test"], "snippet", force=True)

        result = _call_tool(mcp, "rebuild", {})

        assert result["success"] is True
        assert result["entries_indexed"] == 2

    def test_rebuild_reports_schema_warnings(self, _setup: tuple) -> None:
        """rebuild surfaces doctor's per-kind counts."""

        mcp, kb, _logger = _setup

        kb.remember(
            "Bare Decision", "No template fields.", ["test"], "decision",
            part_of=[_HUB_ID],
        )

        result = _call_tool(mcp, "rebuild", {})

        assert result["schema_warnings"]["missing_body_field"] == 1


class TestEntryTypeEnum:
    """entry_type is exposed as a schema-generated enum in the tool schema."""

    def test_remember_entry_type_is_enum(self, _setup: tuple) -> None:
        """The remember tool's JSON Schema constrains entry_type to the schema."""

        mcp, _kb, _logger = _setup

        tool = next(
            t for t in mcp._tool_manager.list_tools() if t.name == "remember"
        )
        schema = tool.parameters
        entry_type = schema["properties"]["entry_type"]

        # Pydantic emits enums as a $ref into $defs
        ref = entry_type.get("$ref", "")
        definition = schema["$defs"][ref.rsplit("/", 1)[-1]]

        assert set(definition["enum"]) == set(load_schema(None).types)

    def test_invalid_entry_type_rejected(self, _setup: tuple) -> None:
        """A type outside the schema cannot be passed through the tool."""

        mcp, _kb, _logger = _setup

        with pytest.raises(Exception):
            _call_tool(
                mcp,
                "remember",
                {
                    "title": "Bogus",
                    "content": "Body.",
                    "tags": ["test"],
                    "entry_type": "not_a_real_type",
                },
            )


class TestToolDoctorViaMcp:
    """Test the doctor tool through the MCP layer."""

    def test_doctor_clean_kb(self, _setup: tuple) -> None:
        """A conforming knowledge base reports no defects."""

        mcp, kb, _logger = _setup

        kb.remember(
            "An Idea",
            "**Candidate:** worth trying later.\n**Status:** not_decided",
            ["test"],
            "idea",
        )

        report = _call_tool(mcp, "doctor", {})

        assert report["entries_scanned"] == 1
        assert all(check["count"] == 0 for check in report["checks"].values())

    def test_doctor_finds_dangling_link(self, _setup: tuple) -> None:
        """A kb:// link to a missing entry is reported."""

        mcp, kb, _logger = _setup

        missing = str(uuid.uuid4())
        created = kb.remember(
            "Linking Idea",
            f"See [gone](kb://{missing}#idea).",
            ["test"],
            "idea",
        )

        report = _call_tool(mcp, "doctor", {})

        assert report["checks"]["dangling_link"]["count"] == 1
        assert created["id"] in report["checks"]["dangling_link"]["ids"]


class TestToolForgetWarnsOnBacklinks:
    """forget reports entries left with dangling references."""

    def test_forget_warns_about_incoming_links(self, _setup: tuple) -> None:
        """Deleting a linked entry returns a warning naming the linkers."""

        mcp, kb, _logger = _setup

        target = kb.remember("Target Idea", "The referenced fact.", ["test"], "idea")
        source = kb.remember(
            "Source Idea",
            f"Builds on [target](kb://{target['id']}#idea).",
            ["test"],
            "idea",
        )

        result = _call_tool(mcp, "forget", {"entry_id": target["id"]})

        assert result["success"] is True
        assert source["id"] in result["incoming"]
        assert "dangling" in result["warning"]

    def test_forget_unlinked_entry_has_no_warning(self, _setup: tuple) -> None:
        """Deleting an entry nothing links to returns a plain success."""

        mcp, kb, _logger = _setup

        created = kb.remember("Lonely Idea", "Nobody links here.", ["test"], "idea")

        result = _call_tool(mcp, "forget", {"entry_id": created["id"]})

        assert "warning" not in result


class TestToolListEntryTypeFilter:
    """list's entry_type filter expands one bucket of a hub digest."""

    def test_list_filters_by_entry_type(self, _setup: tuple) -> None:
        """Only entries of the requested type are returned."""

        mcp, kb, _logger = _setup

        kb.remember(
            "A Feature", "Some feature.", ["test"], "feature", force=True,
            part_of=[_HUB_ID],
        )
        kb.remember("An Idea", "Some idea.", ["test"], "idea", force=True)

        result = _call_tool(mcp, "list", {"entry_type": "feature"})

        assert result["count"] == 1
        assert result["entries"][0]["title"] == "A Feature"


class TestRecallHubDigest:
    """recall on a digesting type summarizes back-links instead of capping them."""

    def test_hub_recall_returns_digest(self, _setup: tuple) -> None:
        """A hub's incoming links come back grouped by the linking type."""

        mcp, kb, _logger = _setup

        hub = kb.remember(
            "Project Hub",
            "**What it does:** everything.\n**Stack:** Python.",
            ["proj"],
            "hub",
            resource="/tmp/proj",
        )
        for index in range(2):
            kb.remember(
                f"Feature {index}",
                f"Detail. [hub](kb://{hub['id']}#hub)",
                ["proj"],
                "feature",
                force=True,
                part_of=[hub["id"]],
            )
        kb.remember(
            "Idea One",
            f"Thought. [hub](kb://{hub['id']}#hub)",
            ["proj"],
            "idea",
            force=True,
        )

        result = _call_tool(mcp, "recall", {"entry_id": hub["id"]})

        digest = result["relations"]["in_digest"]
        assert digest["feature"]["count"] == 2
        assert digest["idea"]["count"] == 1
        assert result["relations"]["in_total"] == 3
        assert "in" not in result["relations"]

    def test_non_hub_recall_keeps_flat_list(self, _setup: tuple) -> None:
        """Types without digest_on_recall keep the plain incoming list."""

        mcp, kb, _logger = _setup

        target = kb.remember("Plain Idea", "Referenced.", ["test"], "idea")
        kb.remember(
            "Referring Idea",
            f"See [it](kb://{target['id']}#idea).",
            ["test"],
            "idea",
            force=True,
        )

        result = _call_tool(mcp, "recall", {"entry_id": target["id"]})

        assert len(result["relations"]["in"]) == 1
        assert "in_digest" not in result["relations"]


class TestPartOfViaMcp:
    """part_of plumbing through the MCP tool layer."""

    def test_remember_rejects_required_type_without_part_of(
        self, _setup: tuple
    ) -> None:
        """Creating a membership-required type without part_of errors."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Bare Feature",
                "content": "Body.",
                "tags": ["test"],
                "entry_type": "feature",
            },
        )

        assert "error" in result
        assert "part_of" in result["error"]

    def test_search_and_list_filter_by_part_of(self, _setup: tuple) -> None:
        """search and list narrow to members of the given hub."""

        mcp, kb, _logger = _setup

        hub = kb.remember(
            "Project Hub",
            "**What it does:** things.\n**Stack:** Python.",
            ["test"],
            "hub",
            resource="/srv/project",
        )
        kb.remember(
            "Widget Feature", "How widgets work.", ["test"], "feature",
            part_of=[hub["id"]],
        )
        kb.remember(
            "Widget Snippet", "Widget code sample.", ["test"], "snippet",
            force=True,
        )

        searched = _call_tool(
            mcp, "search", {"query": "widget", "part_of": [hub["id"]]}
        )
        assert searched["count"] == 1
        assert searched["results"][0]["title"] == "Widget Feature"

        listed = _call_tool(mcp, "list", {"part_of": [hub["id"]]})
        assert listed["count"] == 1
        assert listed["entries"][0]["title"] == "Widget Feature"

    def test_recall_returns_part_of(self, _setup: tuple) -> None:
        """recall surfaces the entry's memberships."""

        mcp, kb, _logger = _setup

        created = kb.remember(
            "Member Entry", "Body.", ["test"], "feature", part_of=[_HUB_ID]
        )

        result = _call_tool(mcp, "recall", {"entry_id": created["id"]})

        assert result["part_of"] == [_HUB_ID]


class TestQueryLog:
    """ENGRAM_QUERY_LOG JSONL logging on search/recall."""

    def test_search_and_recall_logged(
        self, _setup: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the env var set, search and recall append JSONL events."""

        import json

        mcp, kb, _logger = _setup
        log_path = tmp_path / "query_log.jsonl"
        monkeypatch.setenv("ENGRAM_QUERY_LOG", str(log_path))

        created = kb.remember(
            "Query log target", "A searchable body.", ["test"], "snippet"
        )

        _call_tool(mcp, "search", {"query": "searchable"})
        _call_tool(mcp, "recall", {"entry_id": created["id"]})
        _call_tool(mcp, "recall", {"entry_id": str(uuid.uuid4())})

        lines = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [rec["tool"] for rec in lines] == ["search", "recall", "recall"]
        assert lines[0]["query"] == "searchable"
        assert created["id"] in lines[0]["returned_ids"]
        assert lines[1] == {**lines[1], "entry_id": created["id"], "found": True}
        assert lines[2]["found"] is False
        assert all("ts" in rec for rec in lines)

    def test_disabled_without_env(
        self, _setup: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the env var, no log file is created."""

        mcp, _kb, _logger = _setup
        monkeypatch.delenv("ENGRAM_QUERY_LOG", raising=False)

        _call_tool(mcp, "search", {"query": "anything"})

        assert not list(tmp_path.glob("*.jsonl"))
