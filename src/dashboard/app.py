"""
Engram dashboard — FastAPI presentation layer over KnowledgeBase.

Thin wrappers only: every endpoint delegates straight to KnowledgeBase
methods, the same ones server.py's MCP tools call, so remember/delete
behavior (atomicity, duplicate detection, supersede) is identical whether
an entry is edited via MCP or via this dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import KnowledgeBase

_STATIC_DIR = Path(__file__).parent / "static"


class EntryIn(BaseModel):
    """Request body for creating or updating an entry."""

    title: str
    content: str
    tags: list[str]
    entry_type: str
    # None (omitted) keeps an existing entry's resource on PATCH; "" clears it
    resource: str | None = None
    supersede: bool = False


def create_app(kb_resolver: Callable[[Request], KnowledgeBase]) -> FastAPI:
    """
    Build the dashboard FastAPI app.

    Args:
        kb_resolver: Resolves the KnowledgeBase to read/write through for
            a given request. Single-tenant callers pass a resolver that
            always returns the same fixed instance. Multi-tenant callers
            pass a resolver that reads the session (team role: the
            session's own team; admin role: the `?team=` query param) and
            raises `HTTPException(401)` when there is no valid session —
            every route below propagates that as-is, and `index()`
            additionally turns it into a redirect to `/login`.

    Returns:
        Configured FastAPI application.
    """

    app = FastAPI(title="Engram Dashboard")

    def _check_type(kb: KnowledgeBase, entry_type: str) -> None:
        """
        Reject a write whose type is not declared in the schema.

        The MCP tool gets this from its generated `entry_type` enum, which
        the client enforces; this is the same gate for the REST path, so
        neither writer can introduce a type `doctor` will later flag.

        Args:
            kb: KnowledgeBase whose schema governs this request.
            entry_type: Type name from the request body.

        Raises:
            HTTPException: 400 when the type is not in the schema.
        """

        if kb.schema.rule(entry_type) is None:
            allowed = ", ".join(sorted(kb.schema.types))
            raise HTTPException(
                status_code=400,
                detail=f"Unknown entry type '{entry_type}'. Allowed: {allowed}",
            )

    @app.get("/api/graph")
    def get_graph(request: Request, include_superseded: bool = False) -> dict[str, Any]:
        return kb_resolver(request).get_graph(include_superseded=include_superseded)

    @app.get("/api/search")
    def search_entries(
        request: Request,
        q: str,
        tags: list[str] | None = None,
        limit: int = 20,
        include_superseded: bool = False,
        entry_type: str | None = None,
    ) -> dict[str, Any]:
        results = kb_resolver(request).search(
            q,
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=entry_type,
        )
        return {"count": len(results), "results": results}

    @app.get("/api/entries")
    def list_entries(
        request: Request,
        tags: list[str] | None = None,
        limit: int = 500,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        entries = kb_resolver(request).list_entries(
            tags=tags, limit=limit, include_superseded=include_superseded
        )
        return {"count": len(entries), "entries": entries}

    @app.get("/api/entries/{entry_id}")
    def get_entry(request: Request, entry_id: str) -> dict[str, Any]:
        entry = kb_resolver(request).get(entry_id, with_relations=True)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        return entry

    @app.post("/api/entries")
    def create_entry(request: Request, body: EntryIn) -> dict[str, Any]:
        kb = kb_resolver(request)
        _check_type(kb, body.entry_type)
        result = kb.remember(
            body.title,
            body.content,
            body.tags,
            body.entry_type,
            resource=body.resource,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.patch("/api/entries/{entry_id}")
    def update_entry(request: Request, entry_id: str, body: EntryIn) -> dict[str, Any]:
        kb = kb_resolver(request)
        _check_type(kb, body.entry_type)
        result = kb.remember(
            body.title,
            body.content,
            body.tags,
            body.entry_type,
            entry_id=entry_id,
            resource=body.resource,
            supersede=body.supersede,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.delete("/api/entries/{entry_id}")
    def delete_entry(request: Request, entry_id: str) -> dict[str, Any]:
        success = kb_resolver(request).delete(entry_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        return {"success": True, "id": entry_id}

    @app.get("/api/tags")
    def get_tags(request: Request) -> dict[str, Any]:
        tag_list = kb_resolver(request).list_tags()
        return {"count": len(tag_list), "tags": tag_list}

    @app.get("/api/analytics")
    def get_analytics(request: Request) -> dict[str, Any]:
        return kb_resolver(request).get_analytics_snapshot()

    @app.get("/")
    def index(request: Request) -> Response:
        try:
            kb_resolver(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse("/login")
            raise
        return FileResponse(_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app
