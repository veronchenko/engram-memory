"""
Engram dashboard — web UI entry point.

Runs a FastAPI app (see dashboard/app.py) on its own host/port, reusing
the same KnowledgeBase/SQLiteBackend pair server.py builds for MCP.
No side effects on import.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from database import KnowledgeBase
from port_utils import find_free_port

from .app import create_app
from search_backend import DEFAULT_EMBEDDING_MODEL, SQLiteBackend


def _env(name: str, default: str | None = None) -> str | None:
    """Read an ENGRAM_* environment variable with a default."""
    return os.environ.get(f"ENGRAM_{name}", default)


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Every argument has an ENGRAM_* environment variable fallback.
    CLI arguments take priority over env vars.

    Returns:
        Parsed arguments namespace.
    """

    parser = argparse.ArgumentParser(description="Engram Dashboard")
    parser.add_argument(
        "--data-path",
        default=_env("DATA_PATH", "/knowledge"),
        help="Root path for knowledge data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    parser.add_argument(
        "--host",
        default=_env("DASHBOARD_HOST", "0.0.0.0"),
        help="Listen address (env: ENGRAM_DASHBOARD_HOST, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env("DASHBOARD_PORT", "8193")),
        help=(
            "Starting port; the first free port at or above this is used "
            "(env: ENGRAM_DASHBOARD_PORT, default: 8193)"
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=_env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=(
            "Model2Vec HuggingFace hub id for semantic search "
            f"(env: ENGRAM_EMBEDDING_MODEL, default: {DEFAULT_EMBEDDING_MODEL})"
        ),
    )

    return parser.parse_args()


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
    index_path = Path(args.data_path) / "index" / "engram.db"
    backend = SQLiteBackend(index_path, embedding_model=args.embedding_model)
    backend.warm_up()
    kb = KnowledgeBase(args.data_path, backend=backend)

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
