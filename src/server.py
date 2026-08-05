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

import dataclasses
import enum
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastmcp import Context, FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from config import ServerSettings, env, parse_server_args
from database import KnowledgeBase
from doctor import MAX_REPORTED_IDS, check_entry
from port_utils import find_free_port
from schema import Schema, build_entry_type_enum, load_schema
from search_backend import SQLiteBackend
from team_admin import TeamAdminStore

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
    kb: KnowledgeBase | Callable[[], KnowledgeBase],
    logger: logging.Logger,
    schema: Schema | None = None,
) -> None:
    """
    Register all MCP tools on the given server instance.

    Args:
        kb: A fixed KnowledgeBase (single-tenant), or a zero-arg callable
            resolving one per call (multi-tenant — see TeamRegistry.get,
            which reads the authenticated team off the current request).
        schema: Entry validation contract. Defaults to the one the
            knowledge base was built with.
    """

    kb_provider = kb if callable(kb) else (lambda: kb)
    schema = schema if schema is not None else kb_provider().schema

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
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        entry_type: EntryType | None = None,
        part_of: list[str] | None = None,
    ) -> dict:
        """
        Search entries by keyword + semantic similarity.

        Args:
            tags: All tags must match. Only tags that already exist in
                the KB narrow results — an unused tag returns nothing.
            limit: Capped at 100.
            include_superseded: Superseded entries are history, not
                current facts — hidden by default. Pass True for "was X
                ever used"/"what did it replace"-style historical questions.
            entry_type: Narrow to one type once the plain-query results
                look mixed or too broad. Leave unset for a first pass.
            part_of: UUIDs of parent entries — only entries that are
                members of all of them (AND logic, like tags). Find one
                with a prior search/list if not already known; which
                type(s) can act as a parent is schema-defined.
        """

        kb = kb_provider()
        limit = max(1, min(limit, 100))
        type_name = entry_type.value if entry_type is not None else None

        logger.info(
            "search: query='%s', tags=%s, limit=%d, entry_type=%s, part_of=%s",
            query,
            tags,
            limit,
            type_name,
            part_of,
        )

        start = time.perf_counter()
        results = kb.search(
            query,
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=type_name,
            part_of=part_of,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        returned_ids = [r["id"] for r in results]

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
            entry_type=type_name,
            part_of=part_of,
            returned_ids=returned_ids,
        )
        kb.log_query_event(
            ts=datetime.now(timezone.utc).isoformat(),
            session_id=ctx.session_id,
            tool="search",
            query_text=query,
            entry_type=type_name,
            returned_ids=returned_ids,
            top_result_id=returned_ids[0] if returned_ids else None,
            hit=bool(returned_ids),
            latency_ms=latency_ms,
        )

        return {"count": len(results), "results": results}

    @mcp.tool()
    def recall(
        entry_id: str, ctx: Context, relations_limit: int = 20, hops: int = 1
    ) -> dict:
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

        kb = kb_provider()
        relations_limit = max(1, min(relations_limit, 100))
        hops = max(1, min(hops, 2))

        logger.info(
            "recall: id=%s, relations_limit=%d, hops=%d",
            entry_id, relations_limit, hops,
        )

        start = time.perf_counter()
        entry = kb.get(
            entry_id,
            with_relations=True,
            relations_limit=relations_limit,
            record_access=True,
            digest=True,
            hops=hops,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        log_query("recall", entry_id=entry_id, found=entry is not None)
        kb.log_query_event(
            ts=datetime.now(timezone.utc).isoformat(),
            session_id=ctx.session_id,
            tool="recall",
            entry_id=entry_id,
            hit=entry is not None,
            latency_ms=latency_ms,
        )

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
        ctx: Context,
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
            part_of: UUIDs of the parent entries this one belongs to.
                Required when creating an entry of a membership-required
                type. Omit on update to keep the existing memberships;
                pass [] to clear.

        Returns:
            The id and what was done, plus any suggested_links worth
            cross-referencing. Warnings are advisory — the write still
            succeeded. A semantic near-duplicate is instead rejected:
            update or supersede that entry, or pass force=True.
        """

        kb = kb_provider()
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

        start = time.perf_counter()
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
        latency_ms = int((time.perf_counter() - start) * 1000)
        kb.log_query_event(
            ts=datetime.now(timezone.utc).isoformat(),
            session_id=ctx.session_id,
            tool="remember",
            entry_type=type_name,
            entry_id=result.get("id"),
            hit="error" not in result,
            latency_ms=latency_ms,
        )

        if "error" not in result:
            content_size = len(content.encode("utf-8"))
            result["size"] = content_size

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

        kb = kb_provider()
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
            part_of: UUIDs of parent entries — only entries that are
                members of all of them. Combine with entry_type to
                expand one bucket of a parent entry's recall digest in
                full.
        """

        kb = kb_provider()
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

        kb = kb_provider()
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

        kb = kb_provider()
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

        kb = kb_provider()
        logger.info("doctor: running integrity checks")

        report = kb.doctor()

        logger.info(
            "doctor: scanned %d entries", report["entries_scanned"]
        )
        return report


# ---------------------------------------------------------------------------
# Multi-tenant routing
# ---------------------------------------------------------------------------


class TeamRegistry:
    """
    Lazily-built, in-process cache of per-team KnowledgeBase instances.

    Multi-tenant mode only. Each team's data lives under
    <data_path>/teams/<folder>/{entries,index}, built against the same
    process-wide schema every team shares. Instances are created on
    first use and kept for the process lifetime — a revoked key simply
    stops resolving to a folder in admin.db (TeamTokenVerifier checks
    the database on every request), so the cache never needs eviction.
    """

    def __init__(
        self, teams_root: Path, schema: Schema, embedding_model: str
    ) -> None:
        self._teams_root = teams_root
        self._schema = schema
        self._embedding_model = embedding_model
        self._cache: dict[str, KnowledgeBase] = {}
        self._lock = threading.Lock()

    def get(self, folder: str) -> KnowledgeBase:
        """Return the cached KnowledgeBase for a team, building it if new."""

        kb = self._cache.get(folder)
        if kb is not None:
            return kb

        with self._lock:
            kb = self._cache.get(folder)
            if kb is None:
                team_path = self._teams_root / folder
                index_path = team_path / "index" / "engram.db"
                backend = SQLiteBackend(
                    index_path, embedding_model=self._embedding_model
                )
                backend.warm_up()
                kb = KnowledgeBase(str(team_path), backend=backend, schema=self._schema)
                self._cache[folder] = kb
            return kb

    def resolve_current(self) -> KnowledgeBase:
        """
        Resolve the KnowledgeBase for the team authenticated on this request.

        Raises:
            PermissionError: no verified access token on the current
                request — shouldn't happen, since FastMCP rejects
                unauthenticated requests before any tool body runs once
                `auth`/`token_verifier` are configured.
        """

        access_token = get_access_token()
        if access_token is None:
            raise PermissionError("No authenticated team for this request")
        return self.get(access_token.client_id)


class TeamTokenVerifier(TokenVerifier):
    """Resolves a bearer token to a team folder via TeamAdminStore."""

    def __init__(self, store: TeamAdminStore) -> None:
        super().__init__()
        self._store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        folder = self._store.verify_key(token)
        if folder is None:
            return None
        return AccessToken(token=token, client_id=folder, scopes=[])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Application entry point — no side effects on import.
    """

    args = parse_args()
    logger = setup_logging()

    if args.multi_tenant:
        _run_multi_tenant(args, logger)
        return

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

    mcp = FastMCP(name="Engram")

    register_tools(mcp, kb, logger, schema)

    logger.info("Starting Engram (%s transport)", args.transport)
    if args.transport == "stdio":
        mcp.run(transport=args.transport)
    else:
        mcp.run(transport=args.transport, host=args.host, port=port)


def _run_multi_tenant(args: ServerSettings, logger: logging.Logger) -> None:
    """
    Start the MCP server in multi-tenant mode — one process serving
    several teams, routed per request by bearer token (TeamRegistry +
    TeamTokenVerifier). See engram_memory decision on multi-tenant VPS
    deployment for the full design.
    """

    if args.transport != "streamable-http":
        raise SystemExit(
            "--multi-tenant requires --transport streamable-http — MCP, "
            "the dashboard, and the admin UI are mounted as one ASGI app "
            "in a single process, which stdio/sse don't fit"
        )
    if not args.public_url:
        raise SystemExit(
            "--multi-tenant requires --public-url/ENGRAM_PUBLIC_URL — the "
            "externally reachable base URL used as the OAuth resource-"
            "server identifier for token validation"
        )
    if not args.admin_api_key:
        raise SystemExit(
            "--multi-tenant requires --admin-api-key/ENGRAM_ADMIN_API_KEY — "
            "the merged admin UI and JSON API refuse to start unauthenticated"
        )

    data_root = Path(args.data_path)
    schema = load_schema(args.data_path)
    teams_root = data_root / "teams"
    store = TeamAdminStore(data_root / "admin.db")
    registry = TeamRegistry(teams_root, schema, args.embedding_model)
    logger.info(
        "Multi-tenant mode: admin_db=%s, teams_root=%s, schema v%d",
        data_root / "admin.db",
        teams_root,
        schema.version,
    )

    port = find_free_port(args.host, args.port)
    if port != args.port:
        logger.info("Port %d in use, using free port %d instead", args.port, port)

    from app import build_multi_tenant_app

    app = build_multi_tenant_app(
        dataclasses.replace(args, port=port), schema, store, registry, logger
    )

    logger.info(
        "Starting Engram (multi-tenant, merged MCP+dashboard+admin) on %s:%d",
        args.host,
        port,
    )
    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
