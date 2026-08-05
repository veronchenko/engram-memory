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
# Usage analytics (query_log) tuning constants
# ---------------------------------------------------------------------------

# query_text is truncated before storage — a full natural-language query is
# rarely needed to spot a KB gap, and unbounded storage would let the log
# grow proportionally to query length instead of query count.
QUERY_LOG_TEXT_TRUNCATE: int = 500

# Click-through: a search's top result counts as "clicked" only if the
# matching recall() happens within this many minutes — an old, unrelated
# recall days later in the same long-lived session shouldn't count.
CLICK_THROUGH_WINDOW_MINUTES: float = 30.0

# Dead entries: never accessed, or not accessed in this many days.
DEAD_ENTRY_STALE_DAYS: float = 90.0

# How many distinct zero-hit queries /api/analytics reports, most frequent first.
ZERO_HIT_QUERIES_LIMIT: int = 50

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
    multi_tenant: bool
    public_url: str
    admin_api_key: str


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
    parser.add_argument(
        "--multi-tenant",
        action="store_true",
        default=env("MULTI_TENANT", "") == "1",
        help=(
            "Serve several teams from one process: --data-path becomes a "
            "root of admin.db + teams/<name>/, and every request must carry "
            "a bearer token issued by `engram add-team` (env: "
            "ENGRAM_MULTI_TENANT=1, default: off). Independent of "
            "--transport — stdio ignores this flag."
        ),
    )
    parser.add_argument(
        "--public-url",
        default=env("PUBLIC_URL", ""),
        help=(
            "Externally reachable base URL (e.g. https://engram.example.com), "
            "required with --multi-tenant — used as the OAuth resource-server "
            "identifier for token validation (env: ENGRAM_PUBLIC_URL)"
        ),
    )
    parser.add_argument(
        "--admin-api-key",
        default=env("ADMIN_API_KEY", ""),
        help=(
            "Required with --multi-tenant: bearer token for the admin JSON "
            "API and the admin/team browser login (env: ENGRAM_ADMIN_API_KEY)"
        ),
    )

    args = parser.parse_args(argv)
    return ServerSettings(
        data_path=args.data_path,
        transport=args.transport,
        host=args.host,
        port=args.port,
        embedding_model=args.embedding_model,
        multi_tenant=args.multi_tenant,
        public_url=args.public_url,
        admin_api_key=args.admin_api_key,
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


# ---------------------------------------------------------------------------
# admin_api.py CLI/env settings
# ---------------------------------------------------------------------------

# Admin API is loopback-only by design (see engram_memory decision on
# multi-tenant VPS deployment) — not proxied by nginx, only reachable via
# `docker exec` + the `engram` CLI.
ADMIN_API_DEFAULT_HOST: str = "127.0.0.1"
ADMIN_API_DEFAULT_PORT: int = 8194


@dataclass(frozen=True)
class AdminApiSettings:
    """Resolved admin API settings (CLI args win over ENGRAM_* env vars)."""

    data_path: str
    host: str
    port: int
    api_key: str


def parse_admin_api_args(argv: list[str] | None = None) -> AdminApiSettings:
    """
    Parse admin API CLI arguments, falling back to ENGRAM_* env vars.

    Args:
        argv: Argument list to parse; defaults to sys.argv[1:].

    Returns:
        Resolved AdminApiSettings.

    Raises:
        SystemExit: no API key was supplied via --api-key or
            ENGRAM_ADMIN_API_KEY — the admin surface refuses to start
            unauthenticated.
    """

    parser = argparse.ArgumentParser(description="Engram Admin API")
    parser.add_argument(
        "--data-path",
        default=env("DATA_PATH", "/knowledge"),
        help="Root path for team data (env: ENGRAM_DATA_PATH, default: /knowledge)",
    )
    parser.add_argument(
        "--host",
        default=env("ADMIN_API_HOST", ADMIN_API_DEFAULT_HOST),
        help=(
            "Listen address (env: ENGRAM_ADMIN_API_HOST, default: "
            f"{ADMIN_API_DEFAULT_HOST}). Keep this on loopback — the admin "
            "API is not meant to be reachable outside the container."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(env("ADMIN_API_PORT", str(ADMIN_API_DEFAULT_PORT))),
        help=(
            "Listen port (env: ENGRAM_ADMIN_API_PORT, default: "
            f"{ADMIN_API_DEFAULT_PORT})"
        ),
    )
    parser.add_argument(
        "--api-key",
        default=env("ADMIN_API_KEY"),
        help="Required bearer token for admin routes (env: ENGRAM_ADMIN_API_KEY)",
    )

    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error(
            "--api-key or ENGRAM_ADMIN_API_KEY is required — the admin API "
            "refuses to start unauthenticated"
        )

    return AdminApiSettings(
        data_path=args.data_path,
        host=args.host,
        port=args.port,
        api_key=args.api_key,
    )
