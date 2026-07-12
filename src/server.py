"""
Engram — persistent knowledge base MCP server with hybrid search.

Provides MCP tools for storing, searching, and managing knowledge entries.
Entries are Markdown files with YAML frontmatter, indexed by SQLite FTS5
(Porter stemming) fused with local Model2Vec semantic embeddings via
Reciprocal Rank Fusion.

Transport: stdio (stdin/stdout for MCP protocol, managed by Claude Code).
Data: Markdown files in --data-path/entries/, search index in --data-path/index/engram.db.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from database import KnowledgeBase
from port_utils import find_free_port
from search_backend import DEFAULT_EMBEDDING_MODEL, SQLiteBackend

# ---------------------------------------------------------------------------
# CLI arguments (all have ENGRAM_* env var fallbacks, args take priority)
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None) -> str | None:
    """Read an ENGRAM_* environment variable with a default."""
    # Environment variable fallback
    return os.environ.get(f"ENGRAM_{name}", default)


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Every argument has an ENGRAM_* environment variable fallback.
    CLI arguments take priority over env vars.

    Returns:
        Parsed arguments namespace.
    """

    parser = argparse.ArgumentParser(description="Engram MCP Server")
    parser.add_argument(
        "--data-path",
        default=_env("DATA_PATH", "/knowledge"),
        help="Root path for knowledge data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=_env("TRANSPORT", "stdio"),
        help="MCP transport (env: ENGRAM_TRANSPORT, default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=_env("HOST", "0.0.0.0"),
        help="Listen address (env: ENGRAM_HOST, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env("PORT", "8192")),
        help=(
            "Starting port for sse/streamable-http transport; the first free "
            "port at or above this is used (env: ENGRAM_PORT, default: 8192). "
            "Unused for stdio transport."
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=_env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=(
            "Model2Vec HuggingFace hub id for semantic search "
            f"(env: ENGRAM_EMBEDDING_MODEL, default: {DEFAULT_EMBEDDING_MODEL})"
        ),
    )

    # Parsed arguments
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    """
    Configure the application logger to stderr.

    All logs go to stderr (stdout/stdin are reserved for MCP stdio
    transport). Use `docker logs` to read them.

    Returns:
        Configured logger instance.
    """

    log = logging.getLogger("engram")
    log.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Stderr handler
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    log.addHandler(handler)

    # Logger configured
    return log


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    kb: KnowledgeBase,
    logger: logging.Logger,
) -> None:
    """
    Register all MCP tools on the given server instance.

    Each tool function is defined as a closure capturing kb and logger
    from the enclosing scope.

    Args:
        mcp: FastMCP server instance to register tools on.
        kb: KnowledgeBase instance for data operations.
        logger: Logger instance for request logging.
    """

    @mcp.tool()
    def search(
        query: str,
        tags: list[str] | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        entry_type: str | None = None,
    ) -> dict:
        """
        Search entries by keyword + semantic similarity.

        Args:
            query: Search query string.
            tags: Filter by tags (AND logic).
            limit: Max results (default 10, capped at 100).
            include_superseded: Include superseded entries (default False).
            entry_type: Filter by exact entry type (e.g. "diagnostic").

        Returns:
            Dict with count and results (id, title, tags, type, snippet, score).
        """

        limit = max(1, min(limit, 100))

        logger.info(
            "search: query='%s', tags=%s, limit=%d, entry_type=%s",
            query,
            tags,
            limit,
            entry_type,
        )

        results = kb.search(
            query,
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=entry_type,
        )

        return {"count": len(results), "results": results}

    @mcp.tool()
    def recall(entry_id: str) -> dict:
        """
        Read a full entry by id, with its graph relations.

        Args:
            entry_id: UUID of the entry.

        Returns:
            Dict with id, title, tags, content, type, resource, relations
            (out/in lists of {type, id, title}), superseded_by/supersedes
            when applicable, size, last_modified — or error if not found.
        """

        logger.info("recall: id=%s", entry_id)

        entry = kb.get(entry_id, with_relations=True)
        if not entry:
            return {"error": f"Entry {entry_id} not found"}

        entry["size"] = len(entry["content"].encode("utf-8"))

        entry_file = kb.entry_path(entry_id)
        if entry_file and entry_file.exists():
            mtime = entry_file.stat().st_mtime
            entry["last_modified"] = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")

        return entry

    @mcp.tool()
    def remember(
        title: str,
        content: str,
        tags: list[str],
        entry_type: str,
        entry_id: str | None = None,
        force: bool = False,
        resource: str = "",
        supersede: bool = False,
    ) -> dict:
        """
        Create or update an entry (upsert).

        Resolution: entry_id updates that entry; otherwise a title match
        updates the closest existing entry; no match creates a new one;
        force=True always creates new, skipping duplicate detection.

        Args:
            title: Entry title.
            content: Entry body (Markdown). May link other entries via
                [label](kb://uuid#type).
            tags: Tags for categorization.
            entry_type: Entry type (e.g. hub, decision, diagnostic,
                procedure, preference, snippet). Filterable via search.
            entry_id: UUID of an existing entry to update.
            force: Skip duplicate detection, always create new.
            resource: Optional path/URI the entry describes.
            supersede: Version the matched entry instead of overwriting
                it in place. No-op if there's no existing entry matched.

        Returns:
            Dict with id, title, action (created/updated/superseded;
            superseded also has previous_id), optional warnings
            (atomicity/size), and suggested_links (list of {id, title,
            score}) when similar entries exist worth cross-referencing.
        """

        logger.info(
            "remember: title='%s', tags=%s, entry_id=%s, force=%s, supersede=%s",
            title,
            tags,
            entry_id,
            force,
            supersede,
        )

        result = kb.remember(
            title,
            content,
            tags,
            entry_type,
            entry_id=entry_id,
            force=force,
            resource=resource,
            supersede=supersede,
        )

        if "error" not in result:
            content_size = len(content.encode("utf-8"))
            result["size"] = content_size

            warnings = []

            if re.search(r"^#{1,6} ", content, re.MULTILINE):
                warnings.append(
                    "Article contains Markdown headers — each article "
                    "should be a single atomic fact. Split into separate "
                    "articles linked with kb:// references."
                )

            paragraphs = [
                p for p in content.split("\n\n") if p.strip()
            ]
            if len(paragraphs) > 3:
                warnings.append(
                    f"Article has {len(paragraphs)} paragraphs — "
                    "an atomic article should have one decision sentence "
                    "plus an optional justification."
                )

            if content_size > 1024:
                warnings.append(
                    f"Article is {content_size} bytes — exceeds 1 KB limit. "
                    "An atomic article should be much shorter."
                )
            elif content_size > 512:
                warnings.append(
                    f"Article is {content_size} bytes — exceeds 512 B target."
                )

            if warnings:
                result["warnings"] = warnings

        return result

    @mcp.tool()
    def forget(entry_id: str) -> dict:
        """
        Delete a knowledge base entry (file and index).

        Args:
            entry_id: UUID of the entry to delete.

        Returns:
            Dict with success status or error if not found.
        """

        logger.info("forget: id=%s", entry_id)

        success = kb.delete(entry_id)
        if not success:
            return {"error": f"Entry {entry_id} not found"}

        return {"success": True, "id": entry_id}

    @mcp.tool(name="list")
    def list_entries(
        tags: list[str] | None = None,
        limit: int = 50,
        include_superseded: bool = False,
    ) -> dict:
        """
        List knowledge base entries, sorted by title.

        Args:
            tags: Optional tag filter (AND logic — all tags must match).
            limit: Maximum entries to return (default: 50).
            include_superseded: Include entries replaced via remember's
                supersede flag (hidden by default — they're history, not
                current facts).

        Returns:
            Dict with entries list (id, title, tags, type).
        """

        limit = max(1, min(limit, 500))

        logger.info("list: tags=%s, limit=%d", tags, limit)

        entries = kb.list_entries(
            tags=tags, limit=limit, include_superseded=include_superseded
        )

        return {"count": len(entries), "entries": entries}

    @mcp.tool()
    def tags() -> dict:
        """
        List all tags in the knowledge base with entry counts.

        Returns:
            Dict with tags list (tag, count), sorted by count descending.
        """

        logger.info("tags")

        tag_list = kb.list_tags()

        return {"count": len(tag_list), "tags": tag_list}

    @mcp.tool()
    def rebuild() -> dict:
        """
        Rebuild the search index from Markdown files.

        Deletes the existing index and reindexes all entries. Use this
        if the index is corrupted or after manual file changes. Also runs
        a non-blocking schema conformance check over all entries.

        Returns:
            Dict with number of entries indexed, and 'schema_warnings'
            (per-kind counts for missing_type, malformed_resource) when
            any entries are flagged.
        """

        logger.info("rebuild: starting full rebuild")

        result = kb.rebuild()
        count = result["count"]

        response: dict = {"success": True, "entries_indexed": count}

        warnings = result.get("warnings", {})
        if any(warnings.values()):
            response["schema_warnings"] = {
                kind: len(ids) for kind, ids in warnings.items() if ids
            }

        logger.info("rebuild: complete — %d entries", count)
        return response


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Application entry point.

    Parses CLI arguments, sets up logging, initializes the knowledge base,
    registers MCP tools, and starts the server. No side effects on import.
    """

    args = parse_args()
    logger = setup_logging()

    logger.info("Initializing knowledge base from %s", args.data_path)
    index_path = Path(args.data_path) / "index" / "engram.db"
    backend = SQLiteBackend(index_path, embedding_model=args.embedding_model)
    backend.warm_up()
    kb = KnowledgeBase(args.data_path, backend=backend)
    logger.info(
        "Knowledge base ready (index=%s, embedding_model=%s)",
        index_path,
        args.embedding_model,
    )

    port = args.port
    if args.transport != "stdio":
        port = find_free_port(args.host, args.port)
        if port != args.port:
            logger.info(
                "Port %d in use, using free port %d instead", args.port, port
            )

    mcp = FastMCP(name="Engram", host=args.host, port=port)

    # Register all tools using kb and mcp
    register_tools(mcp, kb, logger)

    logger.info("Starting Engram (%s transport)", args.transport)
    # Run server
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
