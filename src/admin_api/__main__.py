"""
Engram admin API — process entry point.

Runs the team-registry FastAPI app (see admin_api/app.py) on its own
loopback host/port, backed by <data_path>/admin.db. No side effects on
import.
"""

from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from config import AdminApiSettings, parse_admin_api_args
from team_admin import TeamAdminStore

from .app import create_app


def parse_args() -> AdminApiSettings:
    """Parse admin API CLI arguments (see config.parse_admin_api_args)."""

    return parse_admin_api_args()


def main() -> None:
    """Parse arguments, open the team registry, and serve the admin API."""

    args = parse_args()

    logger = logging.getLogger("engram")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)

    admin_db_path = Path(args.data_path) / "admin.db"
    logger.info("Opening team registry at %s", admin_db_path)
    store = TeamAdminStore(admin_db_path)

    app = create_app(store, args.api_key)

    logger.info("Starting Engram admin API on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
