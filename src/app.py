"""
Engram multi-tenant web app — merges MCP, the dashboard, and the admin UI
into one ASGI app served by a single uvicorn process.

Only used when ENGRAM_MULTI_TENANT=1 (see server.py's _run_multi_tenant).
Single-tenant/stdio deployments never import this module and never pay
for the extra routes (login/admin/dashboard mount).

Route layout:
- POST /login, POST /logout — session-cookie login, team key or admin key.
- GET/POST /admin, /admin/teams[...] — htmx admin UI, admin session only.
- /admin/api/* — the pre-existing bearer-token JSON admin API (engram CLI).
- /mcp — the MCP streamable-http app (its own bearer-token auth, per team).
- / (mounted last, catch-all) — the dashboard, tenant-resolved via session.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from admin_api.app import create_app as create_admin_app
from config import ServerSettings
from dashboard.app import create_app as create_dashboard_app
from database import KnowledgeBase
from schema import Schema
from server import TeamRegistry, TeamTokenVerifier, register_tools
from team_admin import (
    InvalidTeamNameError,
    Team,
    TeamAdminStore,
    TeamAlreadyExistsError,
)

_ADMIN_DIR = Path(__file__).parent / "admin_api"
_templates = Jinja2Templates(directory=str(_ADMIN_DIR / "templates"))


def _find_team(
    store: TeamAdminStore, *, name: str | None = None, folder: str | None = None
) -> Team | None:
    """
    Look up a team by name or folder, re-reading admin.db every call so a
    revocation is visible immediately (no in-memory caching of team state).
    """

    if name:
        return next((t for t in store.list_teams() if t.name == name), None)
    if folder:
        return next((t for t in store.list_teams() if t.folder == folder), None)
    return None


class _McpNoTrailingSlash:
    """
    ASGI passthrough for the bare "/mcp" path (no trailing slash).

    Once mounted at the "/mcp" prefix, mcp_asgi's own route only matches
    a remainder of "/" — a request for exactly "/mcp" strips to an empty
    remainder and 404s (see mcp python-sdk docs/run/asgi.md). Rewriting
    the scope's path in-process — not an HTTP redirect — keeps this
    working for POST/SSE bodies that most HTTP clients won't re-send
    across a redirect.

    Registered as an exact-match Route *before* the "/mcp" Mount below,
    so it only intercepts the bare path; "/mcp/..." still falls through
    to the Mount unchanged (e.g. its own /.well-known/... sub-route).

    Called directly (not via Mount), so there is no prefix-stripping —
    the rewritten path must match mcp_asgi's own internal route ("/",
    since it's built with streamable_http_path="/") rather than "/mcp/".
    """

    def __init__(self, asgi_app) -> None:
        self._app = asgi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope = {**scope, "path": "/", "raw_path": b"/"}
        await self._app(scope, receive, send)


def build_multi_tenant_app(
    args: ServerSettings,
    schema: Schema,
    store: TeamAdminStore,
    registry: TeamRegistry,
    logger: logging.Logger,
) -> FastAPI:
    """
    Build the merged multi-tenant ASGI app.

    Args:
        args: Resolved server settings (host/port/public_url/admin_api_key).
        schema: Entry-type schema, shared by every team's KnowledgeBase.
        store: Team registry (admin.db).
        registry: Lazy per-team KnowledgeBase cache.
        logger: Process logger.

    Returns:
        A FastAPI app whose lifespan runs the MCP session manager — the
        streamable-http sub-app's own lifespan is dead code once mounted,
        so the host app must drive `mcp.session_manager.run()` itself.
    """

    mcp = FastMCP(
        name="Engram",
        host=args.host,
        port=args.port,
        token_verifier=TeamTokenVerifier(store),
        auth=AuthSettings(
            issuer_url=args.public_url,
            resource_server_url=args.public_url,
        ),
        # streamable_http_app()'s internal route defaults to "/mcp"; since
        # it's mounted under the "/mcp" prefix below, moving that internal
        # route to "/" keeps the public endpoint at exactly "/mcp" instead
        # of "/mcp/mcp" (see mcp python-sdk docs/run/asgi.md).
        streamable_http_path="/",
    )
    register_tools(mcp, registry.resolve_current, logger, schema)
    mcp_asgi = mcp.streamable_http_app()

    def _kb_resolver(request: Request) -> KnowledgeBase:
        role = request.session.get("role")
        if role == "team":
            team = _find_team(store, folder=request.session.get("team"))
            if team is None or team.revoked_at is not None:
                raise HTTPException(status_code=401, detail="Team access revoked")
            return registry.get(team.folder)
        if role == "admin":
            # The dashboard's own fetch() calls never carry the `?team=`
            # query string beyond the initial page load, so the first
            # request that does carry it pins the choice in the session
            # and later requests fall back to that.
            name = request.query_params.get("team")
            if name:
                request.session["admin_team"] = name
            else:
                name = request.session.get("admin_team")
            team = _find_team(store, name=name)
            if team is None:
                raise HTTPException(status_code=401, detail="Select a team")
            return registry.get(team.folder)
        raise HTTPException(status_code=401, detail="Login required")

    dashboard_app = create_dashboard_app(_kb_resolver)
    admin_json_app = create_admin_app(store, args.admin_api_key)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Engram", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secrets.token_urlsafe(32),
        https_only=args.public_url.startswith("https://"),
    )

    app.mount("/admin/static", StaticFiles(directory=str(_ADMIN_DIR / "static")), name="admin_static")
    app.mount("/admin/api", admin_json_app)
    app.router.routes.append(Route("/mcp", endpoint=_McpNoTrailingSlash(mcp_asgi)))
    app.mount("/mcp", mcp_asgi)

    @app.get("/login")
    def login_form(request: Request):
        return _templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login_submit(request: Request, key: str = Form(...)):
        if secrets.compare_digest(key, args.admin_api_key):
            request.session["role"] = "admin"
            return RedirectResponse("/admin", status_code=303)

        folder = store.verify_key(key)
        if folder is not None:
            request.session["role"] = "team"
            request.session["team"] = folder
            return RedirectResponse("/", status_code=303)

        return _templates.TemplateResponse(
            request, "login.html", {"error": "Invalid key"}, status_code=401
        )

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def _require_admin_session(request: Request) -> None:
        if request.session.get("role") != "admin":
            raise HTTPException(status_code=401, detail="Admin login required")

    @app.get("/admin")
    def admin_page(request: Request):
        _require_admin_session(request)
        return _templates.TemplateResponse(
            request, "admin.html", {"teams": store.list_teams()}
        )

    @app.post("/admin/teams")
    def admin_add_team(request: Request, name: str = Form(...)):
        _require_admin_session(request)
        new_key = None
        error = None
        try:
            team, api_key = store.add_team(name)
            new_key = {"name": team.name, "api_key": api_key}
        except (InvalidTeamNameError, TeamAlreadyExistsError) as exc:
            error = str(exc)
        return _templates.TemplateResponse(
            request,
            "_teams.html",
            {"teams": store.list_teams(), "new_key": new_key, "error": error},
        )

    @app.post("/admin/teams/{name}/revoke")
    def admin_revoke_team(request: Request, name: str):
        _require_admin_session(request)
        if not store.revoke_team(name):
            raise HTTPException(status_code=404, detail=f"Team {name!r} not found")
        return _templates.TemplateResponse(
            request, "_teams.html", {"teams": store.list_teams(), "new_key": None}
        )

    # Mounted last: catches "/", "/api/*", "/static/*" — the dashboard's
    # own routes are absolute (see dashboard/static/index.html), so it
    # must own the root, not a sub-path.
    app.mount("/", dashboard_app)

    return app
