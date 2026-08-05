"""
Team admin store — multi-tenant API-key -> data-folder registry.

Backs multi-tenant mode (ENGRAM_MULTI_TENANT): one SQLite database
(<data_path>/admin.db) mapping a hashed API key to a team's data folder
name under <data_path>/teams/. Only used when multi-tenant mode is on —
single-tenant/stdio deployments never touch this module.

Keys are generated once (on add_team) and shown to the caller exactly
once; only their SHA-256 hash is persisted, so a compromised admin.db
does not leak usable credentials.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Team folder names double as SQLite/filesystem path components.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_KEY_BYTES = 32


@dataclass(frozen=True)
class Team:
    """A registered team: its data folder and key lifecycle timestamps."""

    name: str
    folder: str
    created_at: str
    revoked_at: str | None


class TeamAlreadyExistsError(ValueError):
    """Raised by add_team when the name is already registered."""


class InvalidTeamNameError(ValueError):
    """Raised when a team name fails the naming rule."""


class TeamAdminStore:
    """
    CRUD over the team registry.

    Args:
        db_path: Path to admin.db. Parent directories are created if
            missing; the database and its schema are created on first use.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS teams (
                    name TEXT PRIMARY KEY,
                    folder TEXT NOT NULL UNIQUE,
                    key_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """
            )

    def add_team(self, name: str) -> tuple[Team, str]:
        """
        Register a new team and generate its API key.

        Args:
            name: Team identifier; also used as the data folder name
                under <data_path>/teams/.

        Returns:
            The stored Team row and the plaintext API key — the key is
            never recoverable again after this call returns.

        Raises:
            InvalidTeamNameError: name fails the naming rule (lowercase
                alnum/underscore/hyphen, 1-64 chars).
            TeamAlreadyExistsError: a team with this name already exists.
        """

        if not _NAME_RE.match(name):
            raise InvalidTeamNameError(f"Invalid team name {name!r}: must match {_NAME_RE.pattern}")

        api_key = secrets.token_urlsafe(_KEY_BYTES)
        key_hash = _hash_key(api_key)
        created_at = datetime.now(UTC).isoformat()

        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO teams (name, folder, key_hash, created_at) VALUES (?, ?, ?, ?)",
                    (name, name, key_hash, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise TeamAlreadyExistsError(f"Team {name!r} already exists") from exc

        return Team(name=name, folder=name, created_at=created_at, revoked_at=None), api_key

    def list_teams(self) -> list[Team]:
        """List all registered teams, active and revoked, by name."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, folder, created_at, revoked_at FROM teams ORDER BY name"
            ).fetchall()

        return [
            Team(
                name=row["name"],
                folder=row["folder"],
                created_at=row["created_at"],
                revoked_at=row["revoked_at"],
            )
            for row in rows
        ]

    def revoke_team(self, name: str) -> bool:
        """
        Mark a team's key as revoked. Idempotent.

        Returns:
            True if the team existed (whether or not it was already
            revoked), False if no team with this name is registered.
        """

        revoked_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE teams SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                (revoked_at, name),
            )
            if cur.rowcount:
                return True
            exists = conn.execute("SELECT 1 FROM teams WHERE name = ?", (name,)).fetchone()
            return exists is not None

    def verify_key(self, api_key: str) -> str | None:
        """
        Resolve a bearer token to its team's data folder.

        Args:
            api_key: Plaintext key from the Authorization header.

        Returns:
            The team's folder name if the key is valid and not revoked,
            else None.
        """

        key_hash = _hash_key(api_key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT folder FROM teams WHERE key_hash = ? AND revoked_at IS NULL",
                (key_hash,),
            ).fetchone()

        return row["folder"] if row else None


def _hash_key(api_key: str) -> str:
    """SHA-256 hex digest of an API key — admin.db never stores plaintext."""

    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
