"""
Tests for admin_api/app.py — the loopback-only team management API.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from admin_api.app import create_app
from team_admin import TeamAdminStore

_API_KEY = "test-admin-secret"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    store = TeamAdminStore(tmp_path / "admin.db")
    app = create_app(store, _API_KEY)
    return TestClient(app)


def _auth(key: str = _API_KEY) -> dict:
    return {"Authorization": f"Bearer {key}"}


class TestAuth:
    def test_missing_credentials_rejected(self, client: TestClient) -> None:
        response = client.get("/teams")
        assert response.status_code in (401, 403)

    def test_wrong_key_rejected(self, client: TestClient) -> None:
        response = client.get("/teams", headers=_auth("wrong-key"))
        assert response.status_code == 401

    def test_correct_key_accepted(self, client: TestClient) -> None:
        response = client.get("/teams", headers=_auth())
        assert response.status_code == 200


class TestAddTeam:
    def test_add_team_returns_key_once(self, client: TestClient) -> None:
        response = client.post("/teams", json={"name": "acme"}, headers=_auth())

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "acme"
        assert body["folder"] == "acme"
        assert len(body["api_key"]) > 20

    def test_duplicate_team_conflicts(self, client: TestClient) -> None:
        client.post("/teams", json={"name": "acme"}, headers=_auth())
        response = client.post("/teams", json={"name": "acme"}, headers=_auth())

        assert response.status_code == 409

    def test_invalid_name_rejected(self, client: TestClient) -> None:
        response = client.post("/teams", json={"name": "Not Valid!"}, headers=_auth())

        assert response.status_code == 400


class TestListTeams:
    def test_list_reflects_added_teams(self, client: TestClient) -> None:
        client.post("/teams", json={"name": "acme"}, headers=_auth())
        client.post("/teams", json={"name": "beta"}, headers=_auth())

        response = client.get("/teams", headers=_auth())

        names = {team["name"] for team in response.json()}
        assert names == {"acme", "beta"}

    def test_list_does_not_leak_api_keys(self, client: TestClient) -> None:
        client.post("/teams", json={"name": "acme"}, headers=_auth())

        response = client.get("/teams", headers=_auth())

        assert "api_key" not in response.json()[0]


class TestRevokeTeam:
    def test_revoke_marks_team_revoked(self, client: TestClient) -> None:
        client.post("/teams", json={"name": "acme"}, headers=_auth())

        response = client.post("/teams/acme/revoke", headers=_auth())

        assert response.status_code == 200
        assert response.json()["revoked_at"] is not None

    def test_revoke_unknown_team_404s(self, client: TestClient) -> None:
        response = client.post("/teams/ghost/revoke", headers=_auth())

        assert response.status_code == 404
