"""
Engram dashboard — web UI entry point.

Runs a FastAPI app (see dashboard/app.py) on its own host/port, reusing
the same KnowledgeBase/SQLiteBackend pair server.py builds for MCP.
No side effects on import.
"""

from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from config import DashboardSettings, parse_dashboard_args
from database import KnowledgeBase
from port_utils import find_free_port

from .app import create_app
from schema import load_schema
from search_backend import SQLiteBackend


def parse_args() -> DashboardSettings:
    """Parse dashboard CLI arguments (see config.parse_dashboard_args)."""

    return parse_dashboard_args()


def main() -> None:
    """Parse arguments, build the knowledge base, and serve the dashboard."""

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

    logger.info("Initializing knowledge base from %s", args.data_path)
    schema = load_schema(args.data_path)
    index_path = Path(args.data_path) / "index" / "engram.db"
    backend = SQLiteBackend(index_path, embedding_model=args.embedding_model)
    backend.warm_up()
    kb = KnowledgeBase(args.data_path, backend=backend, schema=schema)

    app = create_app(kb)

    port = find_free_port(args.host, args.port)
    if port != args.port:
        logger.info(
            "Port %d in use, using free port %d instead", args.port, port
        )

    logger.info("Starting Engram dashboard on %s:%d", args.host, port)
    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
