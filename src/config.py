"""
Central configuration — CLI/env-resolved server settings and tuning constants.

The MCP server (server.py) and the dashboard (dashboard/__main__.py) each
need the same data-path/embedding-model settings; database.py and
search_backend.py each carry tuning constants for their own ranking/gating
logic. Both used to be duplicated or scattered across those modules — this
is the single place either kind of setting is declared and resolved from.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Env var resolution
# ---------------------------------------------------------------------------


def env(name: str, default: str | None = None) -> str | None:
    """Read an ENGRAM_* environment variable with a default."""
    return os.environ.get(f"ENGRAM_{name}", default)


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------

# Default embedding model — multilingual static embeddings (256-dim),
# picked because KB content mixes Russian and English. Overridable via
# ENGRAM_EMBEDDING_MODEL for callers that construct SQLiteBackend directly.
DEFAULT_EMBEDDING_MODEL: str = "minishlab/potion-multilingual-128M"

# ---------------------------------------------------------------------------
# search_backend.py tuning constants
# ---------------------------------------------------------------------------

# Reciprocal Rank Fusion constant (standard value from Cormack et al.,
# also Elasticsearch's default).
RRF_K: int = 60

# Candidate-pool depth is capped at max(limit*5, 50) but also shrunk to
# roughly total_entries // CANDIDATE_FRACTION_DIVISOR on small KBs — a
# flat 50 is a small slice of a KB with thousands of entries, but on a
# KB of a few hundred it's a third to a half of the whole corpus. Originally
# tuned so a staleness boost on any one entry couldn't reorder unrelated
# queries (TODO 14.1's follow-up); staleness no longer touches ranking at
# all (see _apply_staleness), but the corpus-scaled bound is kept anyway —
# it's still a reasonable cap on BM25/vector fusion depth on small KBs.
CANDIDATE_FRACTION_DIVISOR: int = 8

# Cosine similarity below this is treated as "no semantic match" rather
# than ranked noise — unrelated text pairs score roughly -0.05 to 0.15
# under Model2Vec, while genuinely related pairs score 0.3+.
MIN_COSINE_SIMILARITY: float = 0.2

# Query tokens shorter than this are skipped by the exact-match channel —
# short tokens (articles, prepositions) would literal-match almost every
# title/tags value and turn the channel into noise rather than a signal.
MIN_EXACT_TOKEN_LEN: int = 3

# A query needs at least this many distinct tokens that literally match
# something before the exact-match channel contributes at all. One
# matching token ties every entry that contains it at the same score,
# with no second token to break the tie — noise, not signal.
MIN_EXACT_DISCRIMINATING_TOKENS: int = 2

# Higher bar than MIN_COSINE_SIMILARITY: search recall tolerates loose
# matches, but a suggested kb:// link should only surface entries that are
# actually worth cross-referencing. Tuned against the real production KB
# (see Engram diagnostic on entity-resolution prototyping) — 0.45 cleanly
# separated genuinely related entries from the rest in both a topically
# narrow cluster and a distinct-topic cluster.
SUGGESTION_MIN_SIMILARITY: float = 0.45

# Default number of suggested links to return from remember()
SUGGESTION_TOP_K: int = 4

# Write-gate threshold for remember(): a new entry this semantically close
# to an existing one is a near-duplicate and gets rejected unless the caller
# passes force/supersede. Deliberately much higher than
# SUGGESTION_MIN_SIMILARITY (0.45, "worth a cross-reference") — a gate that
# blocks writes needs near-duplicate confidence. Reference point: memori
# (same SQLite+FTS5+vector stack) gates dedup at cosine 0.92.
WRITE_GATE_MIN_SIMILARITY: float = 0.90

# How many above-threshold candidates the write gate looks at. The gate
# needs the closest *live* entry, and superseded versions are filtered out
# one layer up (only KnowledgeBase knows about versioning) — so the buffer
# has to be deep enough that a stack of old versions can't push the live
# duplicate out of the list.
WRITE_GATE_CANDIDATES: int = 20

# Usage-based staleness (memori's formula as the starting point):
# final_score = rrf_score × boost × decay, where boost log-scales access
# frequency and decay halves per STALENESS_HALF_LIFE_DAYS since the entry
# was last accessed. Never-accessed entries get decay 1.0 — a brand-new
# entry must not start life penalized. Computed lazily at search time;
# there is no background sweep.
STALENESS_HALF_LIFE_DAYS: float = 69.0

# ---------------------------------------------------------------------------
# database.py tuning constants
# ---------------------------------------------------------------------------

# Example members listed per type in a hub's relation digest
DIGEST_SAMPLE_PER_TYPE: int = 3

# Default search limit
DEFAULT_SEARCH_LIMIT: int = 10
DEFAULT_LIST_LIMIT: int = 50

# Duplicate detection threshold (SequenceMatcher ratio, 0.0-1.0)
DUPLICATE_THRESHOLD: float = 0.75

# Over-fetch multiplier for search() when filtering out superseded entries,
# so trimming back down to `limit` still returns a full page when possible.
SUPERSEDED_FETCH_MULTIPLIER: int = 3
SUPERSEDED_FETCH_CAP: int = 300

# ---------------------------------------------------------------------------
# Dashboard defaults
# ---------------------------------------------------------------------------

DASHBOARD_DEFAULT_PORT: int = 8193

# ---------------------------------------------------------------------------
# server.py CLI/env settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerSettings:
    """Resolved MCP server settings (CLI args win over ENGRAM_* env vars)."""

    data_path: str
    transport: str
    host: str
    port: int
    embedding_model: str


def parse_server_args(argv: list[str] | None = None) -> ServerSettings:
    """
    Parse MCP server CLI arguments, falling back to ENGRAM_* env vars.

    Args:
        argv: Argument list to parse; defaults to sys.argv[1:].

    Returns:
        Resolved ServerSettings.
    """

    parser = argparse.ArgumentParser(description="Engram MCP Server")
    parser.add_argument(
        "--data-path",
        default=env("DATA_PATH", "/knowledge"),
        help="Root path for knowledge data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=env("TRANSPORT", "stdio"),
        help="MCP transport (env: ENGRAM_TRANSPORT, default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=env("HOST", "0.0.0.0"),
        help="Listen address (env: ENGRAM_HOST, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env("PORT", "8192")),
        help=(
            "Starting port for sse/streamable-http transport; the first free "
            "port at or above this is used (env: ENGRAM_PORT, default: 8192). "
            "Unused for stdio transport."
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=(
            "Model2Vec HuggingFace hub id for semantic search "
            f"(env: ENGRAM_EMBEDDING_MODEL, default: {DEFAULT_EMBEDDING_MODEL})"
        ),
    )

    args = parser.parse_args(argv)
    return ServerSettings(
        data_path=args.data_path,
        transport=args.transport,
        host=args.host,
        port=args.port,
        embedding_model=args.embedding_model,
    )


# ---------------------------------------------------------------------------
# dashboard/__main__.py CLI/env settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardSettings:
    """Resolved dashboard settings (CLI args win over ENGRAM_* env vars)."""

    data_path: str
    host: str
    port: int
    embedding_model: str


def parse_dashboard_args(argv: list[str] | None = None) -> DashboardSettings:
    """
    Parse dashboard CLI arguments, falling back to ENGRAM_* env vars.

    Args:
        argv: Argument list to parse; defaults to sys.argv[1:].

    Returns:
        Resolved DashboardSettings.
    """

    parser = argparse.ArgumentParser(description="Engram Dashboard")
    parser.add_argument(
        "--data-path",
        default=env("DATA_PATH", "/knowledge"),
        help="Root path for knowledge data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    parser.add_argument(
        "--host",
        default=env("DASHBOARD_HOST", "0.0.0.0"),
        help="Listen address (env: ENGRAM_DASHBOARD_HOST, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env("DASHBOARD_PORT", str(DASHBOARD_DEFAULT_PORT))),
        help=(
            "Starting port; the first free port at or above this is used "
            f"(env: ENGRAM_DASHBOARD_PORT, default: {DASHBOARD_DEFAULT_PORT})"
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=(
            "Model2Vec HuggingFace hub id for semantic search "
            f"(env: ENGRAM_EMBEDDING_MODEL, default: {DEFAULT_EMBEDDING_MODEL})"
        ),
    )

    args = parser.parse_args(argv)
    return DashboardSettings(
        data_path=args.data_path,
        host=args.host,
        port=args.port,
        embedding_model=args.embedding_model,
    )
