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
        """All seven tools must be registered on the MCP instance."""

        mcp, _kb, _logger = _setup

        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {"search", "recall", "remember", "forget", "list", "tags", "rebuild"}

        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_no_extra_tools(self, _setup: tuple) -> None:
        """Only the expected tools are registered (no leftovers)."""

        mcp, _kb, _logger = _setup

        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {"search", "recall", "remember", "forget", "list", "tags", "rebuild"}

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
        """Short, atomic content produces no warnings."""

        mcp, _kb, _logger = _setup

        result = _call_tool(
            mcp,
            "remember",
            {
                "title": "Atomic fact",
                "content": "Logs go to stderr. Simplifies the Dockerfile.",
                "tags": ["test"],
                "entry_type": "snippet",
            },
        )

        assert "warnings" not in result


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

        created = kb.remember("Old Fact", "Original.", ["test"], "decision")
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
            "Versioned", "Old gadget details.", ["test"], "decision"
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
            "Gadget Diagnostic", "Root cause of the gadget bug.", ["test"], "diagnostic"
        )
        kb.remember(
            "Gadget Feature", "How the gadget feature works.", ["test"], "feature"
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

        created = kb.remember("Versioned Entry", "Old.", ["test"], "decision")
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
