"""
Engram admin CLI — thin HTTP client for the admin API (see admin_api/app.py).

Meant to be run via `docker exec` inside the same container as the merged
multi-tenant server (MCP + dashboard + admin UI, see app.py), which mounts
the admin JSON API at /admin/api on the main ENGRAM_PORT — loopback access
to that port from inside the container is unaffected by whatever nginx
does with it externally. Reads ENGRAM_ADMIN_API_URL/ENGRAM_ADMIN_API_KEY
from the same environment the container already has, with no separate
configuration step. Uses stdlib urllib only: the CLI's entire job is a
handful of small JSON requests, not worth a new dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import ADMIN_API_DEFAULT_HOST, env

_MERGED_SERVER_DEFAULT_PORT = 8192


def _admin_url() -> str:
    port = env("PORT", str(_MERGED_SERVER_DEFAULT_PORT))
    default = f"http://{ADMIN_API_DEFAULT_HOST}:{port}/admin/api"
    return env("ADMIN_API_URL", default)


def _admin_key() -> str:
    key = env("ADMIN_API_KEY")
    if not key:
        print(
            "error: ENGRAM_ADMIN_API_KEY is not set in this shell — "
            "the admin API needs it too, so it should already be in the "
            "container's environment.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return key


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = _admin_url().rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {_admin_key()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read()).get("detail", exc.reason)
        print(f"error: {detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(
            f"error: could not reach admin API at {url}: {exc.reason}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def _cmd_add_team(args: argparse.Namespace) -> None:
    result = _request("POST", "/teams", {"name": args.name})
    print(f"Team {result['name']!r} created.")
    print(f"API key (shown once — store it now): {result['api_key']}")


def _cmd_list_teams(_args: argparse.Namespace) -> None:
    teams = _request("GET", "/teams")
    if not teams:
        print("No teams registered.")
        return
    for team in teams:
        status = "revoked" if team["revoked_at"] else "active"
        print(f"{team['name']:<24} {status:<8} created {team['created_at']}")


def _cmd_revoke_team(args: argparse.Namespace) -> None:
    result = _request("POST", f"/teams/{args.name}/revoke")
    print(f"Team {result['name']!r} revoked at {result['revoked_at']}.")


def _cmd_backup(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest_file = dest_dir / f"engram-backup-{timestamp}.tar.gz"

    with tarfile.open(dest_file, "w:gz") as tar:
        for name in ("teams", "entries", "index", "admin.db"):
            source = data_path / name
            if source.exists():
                tar.add(source, arcname=name)

    print(f"Backup written to {dest_file}")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the `engram` console script."""

    parser = argparse.ArgumentParser(prog="engram", description="Engram admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_team = subparsers.add_parser("add-team", help="Register a new team and print its API key")
    add_team.add_argument("name")
    add_team.set_defaults(func=_cmd_add_team)

    list_teams = subparsers.add_parser("list-teams", help="List registered teams")
    list_teams.set_defaults(func=_cmd_list_teams)

    revoke_team = subparsers.add_parser("revoke-team", help="Revoke a team's API key")
    revoke_team.add_argument("name")
    revoke_team.set_defaults(func=_cmd_revoke_team)

    backup = subparsers.add_parser(
        "backup", help="Tar-snapshot the knowledge data to a destination directory"
    )
    backup.add_argument(
        "--data-path",
        default=env("DATA_PATH", "/knowledge"),
        help="Root path for knowledge data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    backup.add_argument(
        "--dest", required=True, help="Destination directory for the tar.gz snapshot"
    )
    backup.set_defaults(func=_cmd_backup)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
