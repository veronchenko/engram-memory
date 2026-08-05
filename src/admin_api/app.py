"""
Engram admin API — team registry management for multi-tenant mode.

Loopback-only by design (see engram_memory decision on multi-tenant VPS
deployment): not proxied by nginx, reachable only via `docker exec` +
the `engram` CLI, or a future admin UI running inside the same trust
boundary. Every route requires the ENGRAM_ADMIN_API_KEY bearer token —
a distinct secret from any team's own API key.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from team_admin import (
    InvalidTeamNameError,
    TeamAdminStore,
    TeamAlreadyExistsError,
)

_bearer = HTTPBearer()


class TeamIn(BaseModel):
    """Request body for registering a team."""

    name: str


class TeamOut(BaseModel):
    """A registered team, without its key (see TeamCreatedOut for that)."""

    name: str
    folder: str
    created_at: str
    revoked_at: str | None


class TeamCreatedOut(TeamOut):
    """Response for a freshly created team — the only time the key is shown."""

    api_key: str


def create_app(store: TeamAdminStore, api_key: str) -> FastAPI:
    """
    Build the admin API FastAPI app.

    Args:
        store: Team registry to operate on.
        api_key: Required bearer token for every route.

    Returns:
        Configured FastAPI application.
    """

    app = FastAPI(title="Engram Admin API")

    def _require_admin(
        creds: HTTPAuthorizationCredentials = Depends(_bearer),  # noqa: B008
    ) -> None:
        if not secrets.compare_digest(creds.credentials, api_key):
            raise HTTPException(status_code=401, detail="Invalid admin API key")

    @app.post("/teams", response_model=TeamCreatedOut, dependencies=[Depends(_require_admin)])
    def add_team(body: TeamIn) -> TeamCreatedOut:
        try:
            team, plaintext_key = store.add_team(body.name)
        except InvalidTeamNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TeamAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return TeamCreatedOut(
            name=team.name,
            folder=team.folder,
            created_at=team.created_at,
            revoked_at=team.revoked_at,
            api_key=plaintext_key,
        )

    @app.get("/teams", response_model=list[TeamOut], dependencies=[Depends(_require_admin)])
    def list_teams() -> list[TeamOut]:
        return [
            TeamOut(
                name=t.name,
                folder=t.folder,
                created_at=t.created_at,
                revoked_at=t.revoked_at,
            )
            for t in store.list_teams()
        ]

    @app.post(
        "/teams/{name}/revoke", response_model=TeamOut, dependencies=[Depends(_require_admin)]
    )
    def revoke_team(name: str) -> TeamOut:
        if not store.revoke_team(name):
            raise HTTPException(status_code=404, detail=f"Team {name!r} not found")

        teams = {t.name: t for t in store.list_teams()}
        return TeamOut(
            name=teams[name].name,
            folder=teams[name].folder,
            created_at=teams[name].created_at,
            revoked_at=teams[name].revoked_at,
        )

    return app
