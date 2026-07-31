"""
Tests for app.py — the merged multi-tenant ASGI app (MCP + dashboard + admin UI).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app import build_multi_tenant_app
from config import ServerSettings
from schema import load_schema
from server import TeamRegistry
from team_admin import TeamAdminStore

_ADMIN_KEY = "test-admin-secret"
_EMBEDDING_MODEL = "minishlab/potion-multilingual-128M"


@pytest.fixture()
def app_ctx(tmp_path: Path):
    store = TeamAdminStore(tmp_path / "admin.db")
    _, team_key = store.add_team("acme")
    schema = load_schema(str(tmp_path))
    registry = TeamRegistry(tmp_path / "teams", schema, _EMBEDDING_MODEL)
    args = ServerSettings(
        data_path=str(tmp_path),
        transport="streamable-http",
        host="127.0.0.1",
        port=0,
        embedding_model=_EMBEDDING_MODEL,
        multi_tenant=True,
        public_url="http://testserver",
        admin_api_key=_ADMIN_KEY,
    )
    app = build_multi_tenant_app(args, schema, store, registry, logging.getLogger("test_app"))
    return app, store, team_key


@pytest.fixture()
def client(app_ctx) -> TestClient:
    app, _store, _team_key = app_ctx
    with TestClient(app) as c:
        yield c


class TestLogin:
    def test_wrong_key_rejected(self, client: TestClient) -> None:
        response = client.post("/login", data={"key": "bogus"})
        assert response.status_code == 401

    def test_admin_key_logs_in_and_redirects_to_admin(self, client: TestClient) -> None:
        response = client.post("/login", data={"key": _ADMIN_KEY}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"

    def test_team_key_logs_in_and_redirects_to_root(self, app_ctx) -> None:
        app, _store, team_key = app_ctx
        with TestClient(app) as client:
            response = client.post("/login", data={"key": team_key}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    def test_logout_clears_session(self, client: TestClient) -> None:
        client.post("/login", data={"key": _ADMIN_KEY})
        client.post("/logout")
        response = client.get("/admin")
        assert response.status_code == 401


class TestAdminUI:
    def test_admin_page_requires_admin_session(self, client: TestClient) -> None:
        assert client.get("/admin").status_code == 401

    def test_team_session_cannot_reach_admin_page(self, app_ctx) -> None:
        app, _store, team_key = app_ctx
        with TestClient(app) as client:
            client.post("/login", data={"key": team_key})
            response = client.get("/admin")
        assert response.status_code == 401

    def test_admin_can_add_and_revoke_team(self, client: TestClient) -> None:
        client.post("/login", data={"key": _ADMIN_KEY})

        add = client.post("/admin/teams", data={"name": "globex"})
        assert add.status_code == 200
        assert "globex" in add.text

        revoke = client.post("/admin/teams/globex/revoke")
        assert revoke.status_code == 200


class TestAdminJsonApi:
    def test_bearer_token_still_works_at_prefixed_path(self, client: TestClient) -> None:
        response = client.get("/admin/api/teams", headers={"Authorization": f"Bearer {_ADMIN_KEY}"})
        assert response.status_code == 200
        assert any(t["name"] == "acme" for t in response.json())

    def test_missing_bearer_token_rejected(self, client: TestClient) -> None:
        response = client.get("/admin/api/teams")
        assert response.status_code in (401, 403)


class TestDashboardTenantResolution:
    def test_root_requires_login(self, client: TestClient) -> None:
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (302, 303, 307)
        assert response.headers["location"] == "/login"

    def test_team_session_sees_only_own_kb(self, app_ctx) -> None:
        app, store, team_key = app_ctx
        store.add_team("beta")
        with TestClient(app) as client:
            client.post("/login", data={"key": team_key})
            client.post(
                "/api/entries",
                json={"title": "Acme Only", "content": "Body.", "tags": ["test"], "entry_type": "snippet"},
            )
            response = client.get("/api/entries")

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_admin_session_requires_team_query_param(self, client: TestClient) -> None:
        client.post("/login", data={"key": _ADMIN_KEY})
        response = client.get("/api/entries")
        assert response.status_code == 401

    def test_admin_session_can_view_selected_team(self, client: TestClient) -> None:
        client.post("/login", data={"key": _ADMIN_KEY})
        response = client.get("/api/entries", params={"team": "acme"})
        assert response.status_code == 200

    def test_admin_session_rejects_unknown_team(self, client: TestClient) -> None:
        client.post("/login", data={"key": _ADMIN_KEY})
        response = client.get("/api/entries", params={"team": "ghost"})
        assert response.status_code == 401

    def test_revoked_team_loses_dashboard_access_on_existing_session(self, app_ctx) -> None:
        app, store, team_key = app_ctx
        with TestClient(app) as client:
            client.post("/login", data={"key": team_key})
            assert client.get("/api/entries").status_code == 200

            store.revoke_team("acme")

            response = client.get("/api/entries")
        assert response.status_code == 401


class TestMcpMounted:
    def test_mcp_endpoint_requires_auth(self, client: TestClient) -> None:
        response = client.post("/mcp/", json={}, headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 401

    def test_mcp_endpoint_works_without_trailing_slash(self, client: TestClient) -> None:
        response = client.post("/mcp", json={}, headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 401

    def test_mcp_well_known_route_unaffected(self, client: TestClient) -> None:
        response = client.get("/mcp/.well-known/oauth-protected-resource")
        assert response.status_code == 200
