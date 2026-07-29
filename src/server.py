"""
Engram — persistent knowledge base MCP server with hybrid search.

Provides MCP tools for storing, searching, and managing knowledge entries.
Entries are Markdown files with YAML frontmatter, indexed by SQLite FTS5
(Porter stemming) fused with local Model2Vec semantic embeddings via
Reciprocal Rank Fusion.

Transport: stdio by default; sse/streamable-http also supported (--transport).
Logs go to stderr because stdout/stdin carry the stdio protocol.
Data: Markdown files in --data-path/entries/, search index in --data-path/index/engram.db.
"""

from __future__ import annotations

import enum
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from config import ServerSettings, env, parse_server_args
from database import KnowledgeBase
from doctor import MAX_REPORTED_IDS, check_entry
from port_utils import find_free_port
from schema import Schema, build_entry_type_enum, load_schema
from search_backend import SQLiteBackend

# The `entry_type` enum in the `remember` signature. Declared here as a
# placeholder so the annotation resolves for readers and static analysis;
# register_tools() rebinds it from the loaded schema before defining any
# tool, and the placeholder value is never the one a client sees.
EntryType: type[enum.Enum] = build_entry_type_enum(("unregistered",))

def log_query(tool: str, **fields: object) -> None:
    """
    Append a retrieval event to the JSONL query log, if enabled.

    Controlled by the ENGRAM_QUERY_LOG env var (a file path); a no-op
    when unset. Used by eval harnesses to get ground truth on which
    entries a session actually retrieved. Failures never break the
    calling tool.

    Args:
        tool: Tool name ("search" or "recall").
        **fields: Event payload (query, returned_ids, entry_id, ...).
    """

    log_path = env("QUERY_LOG")
    if not log_path:
        return

    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        **fields,
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger("engram").warning("query log write failed: %s", exc)


def parse_args() -> ServerSettings:
    """Parse MCP server CLI arguments (see config.parse_server_args)."""

    return parse_server_args()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    """
    Configure the application logger to stderr.

    All logs go to stderr (stdout/stdin are reserved for MCP stdio
    transport). Use `docker logs` to read them.
    """

    log = logging.getLogger("engram")
    log.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    log.addHandler(handler)

    return log


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    kb: KnowledgeBase,
    logger: logging.Logger,
    schema: Schema | None = None,
) -> None:
    """
    Register all MCP tools on the given server instance.

    Args:
        schema: Entry validation contract. Defaults to the one the
            knowledge base was built with.
    """

    schema = schema if schema is not None else kb.schema

    # FastMCP derives each tool's JSON Schema from its type hints at
    # decoration time, and this module uses PEP 563 string annotations —
    # so `entry_type: EntryType` below is resolved against these module
    # globals. The enum is generated from the schema, which is why it has
    # to be published here before the tools are defined. Consequences: the
    # schema is fixed for the process (editing schema.json needs a server
    # restart), and a second register_tools() call with a different schema
    # rebinds the enum module-wide rather than per server instance.
    globals()["EntryType"] = build_entry_type_enum(tuple(schema.types))

    @mcp.tool()
    def search(
        query: str,
        tags: list[str] | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> dict:
        """
        Search entries by keyword + semantic similarity.

        Args:
            tags: All tags must match.
            limit: Capped at 100.
            include_superseded: Superseded entries are history, not
                current facts — hidden by default. Pass True for "was X
                ever used"/"what did it replace"-style historical questions.
            entry_type: Exact match, e.g. "diagnostic".
            part_of: Hub UUIDs — only entries that are members of all
                of them (AND logic, like tags).
        """

        limit = max(1, min(limit, 100))

        logger.info(
            "search: query='%s', tags=%s, limit=%d, entry_type=%s, part_of=%s",
            query,
            tags,
            limit,
            entry_type,
            part_of,
        )

        results = kb.search(
            query,
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=entry_type,
            part_of=part_of,
        )

        # access_count/last_accessed/staleness are display-only (don't
        # affect ranking, see _apply_staleness) and last_accessed/staleness
        # drift with wall-clock time on every call — stripped here so the
        # MCP response stays byte-identical across calls/sessions and
        # doesn't needlessly break prompt-cache prefix reuse. Still exposed
        # via recall/doctor where they're used.
        for result in results:
            result.pop("access_count", None)
            result.pop("last_accessed", None)
            result.pop("staleness", None)

        log_query(
            "search",
            query=query,
            tags=tags,
            entry_type=entry_type,
            part_of=part_of,
            returned_ids=[r["id"] for r in results],
        )

        return {"count": len(results), "results": results}

    @mcp.tool()
    def recall(entry_id: str, relations_limit: int = 20, hops: int = 1) -> dict:
        """
        Read a full entry by id, with its graph relations.

        Counts as usage — recalled entries rank higher in later search.

        Args:
            relations_limit: Per direction, capped at 100. Which
                relations a cap drops is unspecified, so raise the limit
                or use the digest rather than assuming the cut ones
                don't matter.
            hops: 1 (default) returns direct relations only. 2 additionally
                walks one more level in the same direction — use it when
                the question is about *how* this entry connects to another
                through an intermediate one, not when you just need this
                entry's own content. Clamped to [1, 2]. On densely-linked
                (digest) types, only widens the 'out' list — 'in_digest'
                always reflects direct back-links.

        Returns:
            The entry with its relations, or an error if not found.
            Densely-linked types return 'in_digest' — back-links counted
            and sampled per linking type — instead of the full 'in'
            list; read a group in full with list(entry_type=...). At
            hops=2, indirect items carry 'hops': 2 and 'via': [...] (the
            hop-1 ids they were reached through) instead of a 'title' —
            recall() that id directly to read it.
        """

        relations_limit = max(1, min(relations_limit, 100))
        hops = max(1, min(hops, 2))

        logger.info(
            "recall: id=%s, relations_limit=%d, hops=%d",
            entry_id, relations_limit, hops,
        )

        entry = kb.get(
            entry_id,
            with_relations=True,
            relations_limit=relations_limit,
            record_access=True,
            digest=True,
            hops=hops,
        )
        log_query("recall", entry_id=entry_id, found=entry is not None)

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
        entry_type: EntryType,
        entry_id: str | None = None,
        force: bool = False,
        resource: str | None = None,
        supersede: bool = False,
        part_of: list[str] | None = None,
    ) -> dict:
        """
        Create or update an entry (upsert).

        Resolution: entry_id, else the closest title match, else create.
        force=True always creates, skipping duplicate detection.

        Args:
            content: Markdown body. Link entries via
                [label](kb://uuid#type), or [label](kb://uuid#type:edge)
                with edge supports/contradicts/related_to (default
                related_to).
            entry_type: Closest match from the allowed values;
                filterable in search and list.
            resource: Path/URI the entry describes. Omit on update to
                keep the existing one; pass "" to clear.
            supersede: Version the matched entry instead of overwriting
                in place. No-op if nothing matched.
            part_of: Hub UUIDs this entry belongs to. Required when
                creating an entry of a membership-required type. Omit
                on update to keep the existing memberships; pass [] to
                clear.

        Returns:
            The id and what was done, plus any suggested_links worth
            cross-referencing. Warnings are advisory — the write still
            succeeded. A semantic near-duplicate is instead rejected:
            update or supersede that entry, or pass force=True.
        """

        # Store the plain string, not the enum member
        type_name = entry_type.value

        logger.info(
            "remember: title='%s', type=%s, tags=%s, entry_id=%s, force=%s, "
            "supersede=%s, part_of=%s",
            title,
            type_name,
            tags,
            entry_id,
            force,
            supersede,
            part_of,
        )

        result = kb.remember(
            title,
            content,
            tags,
            type_name,
            entry_id=entry_id,
            force=force,
            resource=resource,
            supersede=supersede,
            part_of=part_of,
        )

        if "error" not in result:
            content_size = len(content.encode("utf-8"))
            result["size"] = content_size

            # Schema conformance — non-blocking, unlike the type itself,
            # which the enum already rejected client-side. Checked against
            # the stored entry rather than these arguments: an update may
            # inherit fields it didn't pass (resource), and only the
            # stored form knows what the entry actually looks like now.
            # kb.remember may have added its own warnings (e.g. a part_of
            # target that is not a hub) — keep them, don't overwrite.
            stored = kb.get(result["id"])
            warnings = list(result.get("warnings", []))
            if stored:
                warnings.extend(check_entry(stored, schema))

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
        Delete a knowledge base entry.

        Returns:
            Deletion is never blocked. If other entries still link to
            this one, the response names them (capped) as now-dangling —
            fix or drop those links; doctor re-finds them all.
        """

        logger.info("forget: id=%s", entry_id)

        # Read back-links before the row disappears from the index
        incoming = kb.get_relations(entry_id)["in"]

        success = kb.delete(entry_id)
        if not success:
            return {"error": f"Entry {entry_id} not found"}

        response = {"success": True, "id": entry_id}
        if incoming:
            response["warning"] = (
                f"{len(incoming)} entries still link to {entry_id} — "
                "their kb:// references are now dangling."
            )
            response["incoming"] = [rel["id"] for rel in incoming][:MAX_REPORTED_IDS]

        return response

    @mcp.tool(name="list")
    def list_entries(
        tags: list[str] | None = None,
        limit: int = 50,
        include_superseded: bool = False,
        entry_type: str | None = None,
        part_of: list[str] | None = None,
    ) -> dict:
        """
        List knowledge base entries, sorted by title.

        Args:
            tags: All tags must match.
            limit: Capped at 500.
            include_superseded: Superseded entries are history, not
                current facts — hidden by default.
            entry_type: Exact match. Any value is accepted, including
                retired types — that is how leftovers are found.
            part_of: Hub UUIDs — only entries that are members of all
                of them. Combine with entry_type to expand one bucket
                of a hub's recall digest in full.
        """

        limit = max(1, min(limit, 500))

        logger.info(
            "list: tags=%s, limit=%d, entry_type=%s, part_of=%s",
            tags,
            limit,
            entry_type,
            part_of,
        )

        entries = kb.list_entries(
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=entry_type,
            part_of=part_of,
        )

        return {"count": len(entries), "entries": entries}

    @mcp.tool()
    def tags() -> dict:
        """
        List all tags in the knowledge base, most used first.
        """

        logger.info("tags")

        tag_list = kb.list_tags()

        return {"count": len(tag_list), "tags": tag_list}

    @mcp.tool()
    def rebuild() -> dict:
        """
        Rebuild the search index from the stored entries.

        Use after entry files changed outside this server, or if search
        looks inconsistent with them. Runs the integrity checks too,
        without blocking on them.

        Returns:
            Entries indexed, plus 'schema_warnings' (defect kind ->
            entries affected) when anything is flagged — call doctor
            for the ids.
        """

        logger.info("rebuild: starting full rebuild")

        result = kb.rebuild()
        count = result["count"]

        response: dict = {"success": True, "entries_indexed": count}

        checks = result["report"]["checks"]
        flagged = {
            kind: check["count"] for kind, check in checks.items() if check["count"]
        }
        if flagged:
            response["schema_warnings"] = flagged

        logger.info("rebuild: complete — %d entries", count)
        return response

    @mcp.tool()
    def doctor() -> dict:
        """
        Report knowledge base entries that need fixing.

        Finds kb:// links to missing or superseded entries, unrecognized
        types, entries missing a field their type calls for, entries so
        heavily linked they crowd out everything else, and tags that
        duplicate a type name. Read-only.

        Returns:
            Per defect kind: how many entries are affected and which
            ones (the id list is capped; the count is the true total).
        """

        logger.info("doctor: running integrity checks")

        report = kb.doctor()

        logger.info(
            "doctor: scanned %d entries", report["entries_scanned"]
        )
        return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Application entry point — no side effects on import.
    """

    args = parse_args()
    logger = setup_logging()

    logger.info("Initializing knowledge base from %s", args.data_path)
    schema = load_schema(args.data_path)
    index_path = Path(args.data_path) / "index" / "engram.db"
    backend = SQLiteBackend(index_path, embedding_model=args.embedding_model)
    backend.warm_up()
    kb = KnowledgeBase(args.data_path, backend=backend, schema=schema)
    logger.info(
        "Knowledge base ready (index=%s, embedding_model=%s, schema v%d)",
        index_path,
        args.embedding_model,
        schema.version,
    )

    port = args.port
    if args.transport != "stdio":
        port = find_free_port(args.host, args.port)
        if port != args.port:
            logger.info(
                "Port %d in use, using free port %d instead", args.port, port
            )

    mcp = FastMCP(name="Engram", host=args.host, port=port)

    register_tools(mcp, kb, logger, schema)

    logger.info("Starting Engram (%s transport)", args.transport)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
