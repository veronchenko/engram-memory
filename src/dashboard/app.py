"""
Engram dashboard — FastAPI presentation layer over KnowledgeBase.

Thin wrappers only: every endpoint delegates straight to KnowledgeBase
methods, the same ones server.py's MCP tools call, so remember/delete
behavior (atomicity, duplicate detection, supersede) is identical whether
an entry is edited via MCP or via this dashboard.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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
    resource: str = ""
    supersede: bool = False


def create_app(kb: KnowledgeBase) -> FastAPI:
    """
    Build the dashboard FastAPI app.

    Args:
        kb: KnowledgeBase instance to read/write through.

    Returns:
        Configured FastAPI application.
    """

    app = FastAPI(title="Engram Dashboard")

    @app.get("/api/graph")
    def get_graph(include_superseded: bool = False) -> dict:
        return kb.get_graph(include_superseded=include_superseded)

    @app.get("/api/search")
    def search_entries(
        q: str,
        tags: list[str] | None = None,
        limit: int = 20,
        include_superseded: bool = False,
        entry_type: str | None = None,
    ) -> dict:
        results = kb.search(
            q,
            tags=tags,
            limit=limit,
            include_superseded=include_superseded,
            entry_type=entry_type,
        )
        return {"count": len(results), "results": results}

    @app.get("/api/entries")
    def list_entries(
        tags: list[str] | None = None,
        limit: int = 500,
        include_superseded: bool = False,
    ) -> dict:
        entries = kb.list_entries(
            tags=tags, limit=limit, include_superseded=include_superseded
        )
        return {"count": len(entries), "entries": entries}

    @app.get("/api/entries/{entry_id}")
    def get_entry(entry_id: str) -> dict:
        entry = kb.get(entry_id, with_relations=True)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        return entry

    @app.post("/api/entries")
    def create_entry(body: EntryIn) -> dict:
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
    def update_entry(entry_id: str, body: EntryIn) -> dict:
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
    def delete_entry(entry_id: str) -> dict:
        success = kb.delete(entry_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        return {"success": True, "id": entry_id}

    @app.get("/api/tags")
    def get_tags() -> dict:
        tag_list = kb.list_tags()
        return {"count": len(tag_list), "tags": tag_list}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app
