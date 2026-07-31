"""
Tests for team_admin.py — the multi-tenant team registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from team_admin import (
    InvalidTeamNameError,
    TeamAdminStore,
    TeamAlreadyExistsError,
)


@pytest.fixture()
def store(tmp_path: Path) -> TeamAdminStore:
    return TeamAdminStore(tmp_path / "admin.db")


class TestAddTeam:
    def test_add_team_returns_team_and_plaintext_key(self, store: TeamAdminStore) -> None:
        team, api_key = store.add_team("acme")

        assert team.name == "acme"
        assert team.folder == "acme"
        assert team.revoked_at is None
        assert len(api_key) > 20

    def test_add_team_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "admin.db"
        TeamAdminStore(db_path).add_team("acme")

        reopened = TeamAdminStore(db_path)
        assert [t.name for t in reopened.list_teams()] == ["acme"]

    def test_duplicate_name_rejected(self, store: TeamAdminStore) -> None:
        store.add_team("acme")

        with pytest.raises(TeamAlreadyExistsError):
            store.add_team("acme")

    @pytest.mark.parametrize(
        "bad_name", ["", "Acme", "acme team", "-acme", "a" * 65, "acme/../etc"]
    )
    def test_invalid_names_rejected(self, store: TeamAdminStore, bad_name: str) -> None:
        with pytest.raises(InvalidTeamNameError):
            store.add_team(bad_name)

    def test_generated_keys_are_unique(self, store: TeamAdminStore) -> None:
        _, key1 = store.add_team("acme")
        _, key2 = store.add_team("beta")

        assert key1 != key2


class TestVerifyKey:
    def test_valid_key_resolves_to_folder(self, store: TeamAdminStore) -> None:
        team, api_key = store.add_team("acme")

        assert store.verify_key(api_key) == team.folder

    def test_unknown_key_returns_none(self, store: TeamAdminStore) -> None:
        assert store.verify_key("not-a-real-key") is None

    def test_revoked_key_returns_none(self, store: TeamAdminStore) -> None:
        _, api_key = store.add_team("acme")
        store.revoke_team("acme")

        assert store.verify_key(api_key) is None

    def test_plaintext_key_never_persisted(self, store: TeamAdminStore, tmp_path: Path) -> None:
        _, api_key = store.add_team("acme")

        raw = (tmp_path / "admin.db").read_bytes()
        assert api_key.encode("utf-8") not in raw


class TestRevokeTeam:
    def test_revoke_existing_team_returns_true(self, store: TeamAdminStore) -> None:
        store.add_team("acme")

        assert store.revoke_team("acme") is True
        team = next(t for t in store.list_teams() if t.name == "acme")
        assert team.revoked_at is not None

    def test_revoke_unknown_team_returns_false(self, store: TeamAdminStore) -> None:
        assert store.revoke_team("ghost") is False

    def test_revoke_is_idempotent(self, store: TeamAdminStore) -> None:
        store.add_team("acme")

        first = store.revoke_team("acme")
        second = store.revoke_team("acme")

        assert first is True
        assert second is True


class TestListTeams:
    def test_list_is_sorted_by_name(self, store: TeamAdminStore) -> None:
        store.add_team("zeta")
        store.add_team("alpha")

        assert [t.name for t in store.list_teams()] == ["alpha", "zeta"]

    def test_list_includes_revoked_teams(self, store: TeamAdminStore) -> None:
        store.add_team("acme")
        store.revoke_team("acme")

        assert [t.name for t in store.list_teams()] == ["acme"]
