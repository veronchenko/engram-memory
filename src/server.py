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
        help="Listen port (env: ENGRAM_PORT, default: 8192)",
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
    ) -> dict:
        """
        Search the knowledge base using hybrid keyword + semantic search.

        Fuses SQLite FTS5 (BM25, Porter stemming) with cosine similarity
        over local Model2Vec embeddings via Reciprocal Rank Fusion, so
        queries that share no literal words with an entry can still find
        it by meaning.

        Args:
            query: Search query string.
            tags: Optional tag filter (AND logic — all tags must match).
            limit: Maximum results to return (default: 10).

        Returns:
            Dict with results list (id, title, tags, type, snippet, score).
        """

        # Clamp limit to valid range
        limit = max(1, min(limit, 100))

        logger.info("search: query='%s', tags=%s, limit=%d", query, tags, limit)

        results = kb.search(query, tags=tags, limit=limit)

        # Search done
        return {"count": len(results), "results": results}

    @mcp.tool()
    def recall(entry_id: str) -> dict:
        """
        Read the full content of a knowledge base entry.

        Also returns graph relations: outgoing links (from kb://uuid#type
        in content) and incoming backlinks (other articles linking here).

        Args:
            entry_id: UUID of the entry.

        Returns:
            Dict with id, title, tags, content, relations — or error if not found.
            Also includes 'type', 'resource' when set on the entry.
            Relations has 'out' and 'in' lists, each with type, id, title.
        """

        logger.info("recall: id=%s", entry_id)

        entry = kb.get(entry_id, with_relations=True)
        if not entry:
            # Not found
            return {"error": f"Entry {entry_id} not found"}

        # Add size metadata (bytes)
        entry["size"] = len(entry["content"].encode("utf-8"))

        # Add last modified date from filesystem
        entry_file = kb.entry_path(entry_id)
        if entry_file and entry_file.exists():
            mtime = entry_file.stat().st_mtime
            entry["last_modified"] = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")

        # Entry retrieved
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
    ) -> dict:
        """
        Store or update a knowledge base entry (upsert).

        Resolution order:
        1. If entry_id is provided -> update that entry
        2. If no entry_id -> search for similar titles
           - If a match is found -> update the best match
           - If no match -> create a new entry
        3. If force=True -> always create new (skip duplicate detection)

        Atomicity rule: each article MUST contain exactly one piece of
        knowledge — a single decision, fact, or procedure. The content
        should be one sentence stating the decision, plus an optional
        short justification. No Markdown headers, no multi-section
        documents. If you have multiple decisions, create multiple
        articles and link them with kb:// references.

        Content policy: Engram stores ZERO discoverable information.
        If information is derivable from code, git history, configuration
        files, or existing documentation, it does not belong here. Engram
        captures decisions and their context, diagnostics and their root
        causes, procedures learned the hard way — the kind of knowledge
        that is lost when a conversation ends. Write in affirmative style:
        state what is, not what isn't.

        Content may contain links to other entries using the format
        [label](kb://uuid#type) where type is the relation kind (e.g.
        runs-on, depends-on, mirrors). These links are automatically
        indexed as graph relations, queryable via recall.

        Args:
            title: Entry title.
            content: Entry body (Markdown).
            tags: List of tags for categorization.
            entry_type: Required classification (e.g. hub, decision,
                diagnostic, procedure, preference, snippet). Filterable
                via search (type:<value>), separate from tags.
            entry_id: Optional UUID of an existing entry to update.
            force: Skip duplicate detection and always create new.
            resource: Optional canonical URI for the underlying asset
                the entry describes (e.g. a repo path or service URL).

        Returns:
            Dict with id, title, and action ('created' or 'updated').
        """

        logger.info(
            "remember: title='%s', tags=%s, entry_id=%s, force=%s",
            title,
            tags,
            entry_id,
            force,
        )

        result = kb.remember(
            title,
            content,
            tags,
            entry_type,
            entry_id=entry_id,
            force=force,
            resource=resource,
        )

        # Add size metadata and atomicity warnings
        if "error" not in result:
            content_size = len(content.encode("utf-8"))
            result["size"] = content_size

            warnings = []

            # Atomicity: no Markdown headers in content
            if re.search(r"^#{1,6} ", content, re.MULTILINE):
                warnings.append(
                    "Article contains Markdown headers — each article "
                    "should be a single atomic fact. Split into separate "
                    "articles linked with kb:// references."
                )

            # Atomicity: too many paragraphs suggests multiple topics
            paragraphs = [
                p for p in content.split("\n\n") if p.strip()
            ]
            if len(paragraphs) > 3:
                warnings.append(
                    f"Article has {len(paragraphs)} paragraphs — "
                    "an atomic article should have one decision sentence "
                    "plus an optional justification."
                )

            # Size thresholds
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

        # Remember done
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
            # Not found
            return {"error": f"Entry {entry_id} not found"}

        # Forgotten
        return {"success": True, "id": entry_id}

    @mcp.tool(name="list")
    def list_entries(
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> dict:
        """
        List knowledge base entries, sorted by title.

        Args:
            tags: Optional tag filter (AND logic — all tags must match).
            limit: Maximum entries to return (default: 50).

        Returns:
            Dict with entries list (id, title, tags, type).
        """

        # Clamp limit to valid range
        limit = max(1, min(limit, 500))

        logger.info("list: tags=%s, limit=%d", tags, limit)

        entries = kb.list_entries(tags=tags, limit=limit)

        # Listed
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

        # Tags listed
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
        # Rebuild done
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
    kb = KnowledgeBase(args.data_path, backend=backend)
    logger.info(
        "Knowledge base ready (index=%s, embedding_model=%s)",
        index_path,
        args.embedding_model,
    )

    mcp = FastMCP(name="Engram", host=args.host, port=args.port)

    # Register all tools using kb and mcp
    register_tools(mcp, kb, logger)

    logger.info("Starting Engram (%s transport)", args.transport)
    # Run server
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
