# Engram — Persistent Knowledge Base MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/foreigndmitryi/engram)](https://hub.docker.com/r/foreigndmitryi/engram)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](src/pyproject.toml)

Engram is a self-hosted [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents persistent memory across sessions and projects. Markdown files as source of truth, hybrid keyword + semantic search, typed graph relations.

**Docker Hub:** [foreigndmitryi/engram](https://hub.docker.com/r/foreigndmitryi/engram)

## Table of Contents

- [Concept](#concept)
- [Features](#features)
- [Comparison](#comparison)
- [Measured Against a Plain Markdown Wiki](#measured-against-a-plain-markdown-wiki)
- [Design Patterns](#design-patterns)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Search](#search)
- [Tools](#tools)
- [Dashboard](#dashboard)
- [Graph Relations](#graph-relations)
- [Usage Examples](#usage-examples)
- [Prompt Your Agent](#prompt-your-agent)
- [Configuration](#configuration)
- [Storage Format](#storage-format)
- [Development](#development)
- [License](#license)

## Concept

Agent conversations end and take their context with them. Engram is the piece that survives: a knowledge base an agent searches *before* acting and writes to *after* resolving something non-obvious, so the next session — same project or a different one — starts with what was already learned instead of re-deriving it.

It deliberately stores **zero discoverable information**. If a fact can be pulled from code, git history, config files, or existing docs, it does not belong in Engram — that's what greps and re-reads are for. What belongs is the kind of knowledge a conversation would otherwise lose: a decision and the alternatives it ruled out, a bug's root cause and fix, a procedure learned the hard way, a preference stated once that should hold from then on.

Two things keep the base usable as it grows:
- **Atomicity** — one entry, one fact. `remember` warns (non-blocking) on Markdown headers, more than 3 paragraphs, or content past 512 B/1 KB, pushing multi-fact dumps back into separate linked entries instead of a wall of text no search will rank well.
- **The graph, not a pile** — entries link to each other via `kb://uuid#type` references, so related facts (a hub project, its features, a diagnostic tied to one of them) stay navigable both ways instead of living as isolated rows.

## Features

- **Hybrid search** — SQLite FTS5 (BM25, Porter stemming) fused with cosine similarity over local Model2Vec embeddings *and* an IDF-weighted exact-match channel over title/tags via Reciprocal Rank Fusion; finds entries by meaning or by a literal proper noun BM25/embeddings alone would dilute among lexically-similar distractors, with zero cloud dependency
- **Typed graph relations** — `kb://uuid#type` links between entries, resolved both directions (outgoing + backlinks) on every `recall`, with an optional second hop (`hops=2`) to see how two entries connect through an intermediate one
- **Schema-enforced entry types** — `hub`, `decision`, `diagnostic`, `feature`, `procedure`, `integration`, `pattern`, `snippet`, `preference`, `idea` — declared in `schema.json` and exposed to the client as an enum, so an invalid type can't be written; filterable on search/list
- **`doctor` integrity pass** — one schema-driven check over the Markdown files for dangling and superseded links, undeclared types, missing template fields, supernodes, and tag/type collisions
- **`part_of` structural membership** — links a detail entry (`decision`, `diagnostic`, `feature`, `procedure`, `integration`, ...) to its hub, enforced per type by the schema; filterable on `search`/`list`, grouped alongside `kb://` back-links in a hub's `recall` digest
- **Bi-temporal versioning** — `remember(..., supersede=True)` creates a new version instead of overwriting; old versions stay in history (`include_superseded=True`) instead of being lost
- **Duplicate detection & link suggestions** — `remember` matches near-identical titles to avoid duplicate entries, and returns `suggested_links` (embedding-similarity matches) so related facts get cross-referenced instead of orphaned
- **Atomicity guardrails** — non-blocking warnings on structural anti-patterns (headers, >3 paragraphs, oversized content) so the base stays one-fact-per-entry as it scales
- **Web dashboard** — force-directed graph view, hybrid search, and a CRUD panel over the same knowledge base the MCP tools use (see [Dashboard](#dashboard))
- **Three transports** — stdio (agent-managed), SSE, streamable-http — so the same server works for a single local agent or a shared multi-agent deployment
- **Markdown as source of truth** — the SQLite index is a rebuildable cache; delete it and `rebuild`, no data is ever lost

## Comparison

| | Engram | Mem0 | Zep / Graphiti | LangMem |
|---|---|---|---|---|
| Source of truth | Markdown files on disk | Vector DB / managed API | Temporal knowledge graph | Vector store (LangChain-backed) |
| Search | BM25 + local Model2Vec embeddings (RRF fusion) | Vector similarity | Graph traversal + embeddings | Vector similarity |
| Relations | Explicit `kb://uuid#type` links, agent-authored | Implicit (LLM-extracted facts) | Auto-extracted temporal graph edges | None built-in |
| Temporal model | Bi-temporal `valid_at`/`supersede` on write | Fact overwrite | Native temporal graph (bi-temporal edges) | None built-in |
| Deployment | Self-hosted, single Docker image, no cloud dependency | Hosted API or self-hosted + vector DB | Self-hosted, needs Neo4j | Library, no server |
| Design center | Deliberately minimal — zero discoverable info, agent decides what's worth keeping | Automatic fact extraction from conversations | Automatic entity/relationship extraction | Composable memory primitives for LangGraph agents |

Engram trades automatic extraction (Mem0, Zep) for an agent-curated, atomic, explicitly-linked knowledge base — no LLM-driven ingestion pipeline, no graph database dependency, and the on-disk Markdown stays human-readable and diffable.

## Measured Against a Plain Markdown Wiki

Engram was benchmarked against the same knowledge base packaged as an ordinary Markdown wiki — one file per topic, organized in folders by project and category (decision, diagnostic, feature, procedure, ...), each project with an index page and cross-links between related pages, browsed with `Read`/`Grep`/`Glob`/`Bash`. Same content, two ways of finding it — for equivalent fact coverage, Engram's `search`/`recall` used:

| | Engram | Wiki | Improvement |
|---|---|---|---|
| Tool calls | 104 | 186 | **-44%** |
| Total tokens | 3.87M | 5.91M | **-35%** |
| Cost | $1.31 | $1.62 | **-19%** |
| Wall time (sum) | 577s | 760s | **-24%** |
| Fact rate | 1.000 | 0.964 | **+3.7%** |

32 questions (single-hop, multi-hop, negative, cross-lingual, supersede) over **5,070 entries** — 70 curated facts across 4 fictional projects plus 5,000 real-text distractor entries, so retrieval has to work at a scale that doesn't fit in an agent's context:

```
wiki/
├── Ledgerbird/                       )
├── Pipewren/                         )  4 curated projects — 70 real
├── Snipfox/                          )  decisions/diagnostics/features/
├── Featherstore/                     )  procedures/integrations/snippets
│   ├── README.md                        <- project index page, links to every entry below
│   ├── decision/
│   │   ├── offline-engine-duckdb-over-spark.md
│   │   ├── online-value-serialization-msgpack.md
│   │   └── ... (2 more)
│   ├── diagnostic/
│   │   ├── redis-memory-doubling-from-ttl-less-deprecated-feature-groups.md
│   │   └── training-serving-skew-from-tz-naive-event-timestamps.md
│   ├── feature/       (4 entries)
│   ├── integration/   (2 entries)
│   ├── procedure/     (1 entry)
│   └── snippet/       (1 entry)
├── _shared/                             cross-project patterns & preferences
└── haystack-project-00000.../00199/     200 distractor projects x 25 pages
    │                                     = 5,000 real-text (Wikipedia) pages
    ├── README.md                        <- same index-page shape as a real project
    ├── decision/   (2 entries)
    ├── feature/    (3 entries)
    ├── idea/       (7 entries)
    ├── pattern/    (4 entries)
    ├── procedure/  (1 entry)
    └── snippet/    (2 entries)
```

Every category folder is a flat list of one-file-per-entry, and every project folder (real or distractor) has its own `README.md` index page linking to all of them — structurally identical, so the wiki arm can't tell curated fact from distractor by shape alone.

An agent that already knows *where* to look doesn't need to `grep`, re-read, and re-`grep` its way there — the efficiency edge holds for equivalent fact coverage. Retrieval ranking has also been improving: adding an IDF-weighted exact-match channel (below) moved MRR from 0.851 to 0.857 and recall@5 from 0.851 to 0.869, with no regression across any language.

## Design Patterns

- **Reciprocal Rank Fusion** — BM25 and embedding rankings are computed independently and merged by rank position rather than raw score, avoiding the need to normalize incomparable similarity metrics.
- **Rebuildable cache over source of truth** — the SQLite index is fully derived from the Markdown files (`rebuild` regenerates it from scratch); the database is never the only copy of a fact.
- **Bi-temporal versioning** — `supersede` writes a new entry and points the old one at it via `superseded_by`, instead of overwriting in place, so history stays queryable (`include_superseded`).
- **HATEOAS-style graph traversal** — every `recall` response carries its own outgoing/incoming `kb://` links, so navigating the knowledge graph doesn't require a separate query per hop.
- **Shared domain layer, two transports** — the dashboard's REST API and the MCP tools both call the same `KnowledgeBase`/`SQLiteBackend` methods, so there is exactly one code path for writes regardless of which surface triggered them.

## Architecture

```
Markdown files  --->  Search index  --->  MCP  --->  Agent
(source of truth)     (SQLite FTS5 +      (server.py)  (Claude Code,
                        Model2Vec,                       ChatGPT, ...)
                        rebuildable cache)
```

Markdown files in `<data-path>/entries/` are the only source of truth. The SQLite index (`search_backend.py`) is a disposable cache built from them — BM25 + Model2Vec embeddings fused via Reciprocal Rank Fusion, plus the `kb://` relation graph — and can always be regenerated with `rebuild`. `server.py` exposes that index to an agent as MCP tools (`remember`/`recall`/`search`/...); the dashboard is an alternate entry point at the same layer, hitting the same `KnowledgeBase`/index directly over REST instead of MCP, so `remember`/`delete` behave identically whether called by an agent or edited by hand in the browser.

- **`src/server.py`** — MCP tool definitions (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`, `doctor`), one process, stdio/SSE/streamable-http transport
- **`src/database.py`** — `KnowledgeBase`: Markdown + YAML frontmatter CRUD, UUID assignment, duplicate detection, bi-temporal supersede logic
- **`src/schema.json` + `src/schema.py`** — the entry taxonomy and its per-type rules as data, plus the loader that turns them into the `entry_type` enum the MCP client validates against
- **`src/doctor.py`** — the schema-driven integrity pass shared by the `doctor` tool, `rebuild`'s warnings, and `remember`'s conformance check
- **`src/search_backend.py`** — `SQLiteBackend`: BM25 (FTS5) fused with Model2Vec cosine similarity via Reciprocal Rank Fusion, plus `kb://` relation extraction/graph traversal; embeddings are computed lazily on write and stored as a BLOB column
- **`src/dashboard/`** — a second, optional process (`app.py` FastAPI REST + `/api/graph`, `static/index.html` vanilla-JS canvas graph, `__main__.py` its own uvicorn entry point) reusing the same `KnowledgeBase`
- **`plugins/engram-hooks/`** — a self-contained plugin installable in both Claude Code and Codex (`.claude-plugin/plugin.json`, `SessionStart`/`Stop`/`SessionEnd` + a `PreToolUse` search-before-remember gate, plus a Claude-Code-only `engram-project-onboarder` agent) that mechanically enforces the search-first, remember-after workflow instead of relying on a system prompt alone; listed as `engram-hooks` in both the repo-root `.claude-plugin/marketplace.json` (Claude Code) and `plugins/marketplace.json` (Codex)

In Docker, the MCP server and the dashboard run as two processes in one container (`docker-entrypoint.sh`), sharing the same `/knowledge` volume; the container exits if either process dies.

## Quick Start

### stdio

Your agent manages the server. Recommended for Claude Code, ChatGPT Desktop, Cursor.

```bash
claude mcp add --transport stdio engram -- \
  docker run -i --rm -v ./knowledge:/knowledge foreigndmitryi/engram
```

### SSE

Persistent server on the network. Share knowledge across multiple agents.

```bash
docker run -d --name engram \
  -p 8192 \
  -v ./knowledge:/knowledge \
  foreigndmitryi/engram --transport sse

docker port engram 8192   # host port Docker assigned
claude mcp add --transport sse engram http://your-host:<port>/sse
```

`-p 8192` (host port omitted) has Docker pick a free ephemeral host port
instead of failing when a fixed port like 8192 is already taken by
another container — check the actual assignment with `docker port`.
Use `-p 8192:8192` instead if you need the host port to stay fixed.

### HTTP

Stateless, load-balanceable.

```bash
docker run -d --name engram \
  -p 8192 \
  -v ./knowledge:/knowledge \
  foreigndmitryi/engram --transport streamable-http

docker port engram 8192   # host port Docker assigned
claude mcp add --transport http engram http://your-host:<port>/mcp
```

### Multi-tenant

One server, several teams, each isolated to its own data folder and API key. Set `ENGRAM_MULTI_TENANT=1` (requires `ENGRAM_PUBLIC_URL` and `ENGRAM_ADMIN_API_KEY`, and a network transport — never stdio); `src/app.py` then serves MCP, the admin UI, and the dashboard from one process. Provision a team with `engram add-team <name>` (run via `docker exec`, see [`src/cli.py`](src/cli.py)) to get back its API key, then point each team's agent at `/mcp` with that key as a bearer token:

```bash
claude mcp add --transport http engram https://your-host/mcp \
  --header "Authorization: Bearer <TEAM_API_KEY>"
```

Or as a raw `mcpServers` config (Claude Desktop and other clients):

```json
{
  "mcpServers": {
    "engram": {
      "type": "http",
      "url": "https://your-host/mcp",
      "headers": {
        "Authorization": "Bearer <TEAM_API_KEY>"
      }
    }
  }
}
```

`TeamTokenVerifier` (`src/server.py`) hashes the token and looks it up in `admin.db` on every request — no caching, so a revoked key stops working immediately.

## Search

Hybrid: SQLite FTS5 (Porter stemming, BM25) fused with cosine similarity over local [Model2Vec](https://github.com/MinishLab/model2vec) embeddings (`minishlab/potion-multilingual-128M`, no cloud dependency) via Reciprocal Rank Fusion — so a query that shares no literal words with an entry can still find it by meaning. Falls back to keyword-only if the embedding model can't load. The index is still a single SQLite file you can query with standard SQL tools.

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `remember` | Create or update an entry (upsert with duplicate detection, or version it via `supersede`). `entry_type` is required, and `part_of` (hub UUIDs) is required/optional/rejected per type per the schema's `membership` rule. Returns `size`, non-blocking atomicity `warnings` (Markdown headers, >3 paragraphs, >512 B / >1 KB), and `suggested_links` — near-duplicate/related entries (by embedding similarity) worth cross-referencing with a `kb://` link. | `title`, `content`, `tags`, `entry_type`, `entry_id`, `force`, `resource`, `supersede`, `part_of` |
| `recall` | Read an entry with its graph relations (outgoing + backlinks). Returns `size` and `last_modified`. High-degree types (hubs) return `in_digest` — back-links grouped by linking type, plus members grouped by `part_of` — instead of an arbitrarily truncated list. | `entry_id`, `relations_limit` |
| `search` | Hybrid keyword + semantic search, filterable by tags, exact `entry_type`, and/or `part_of` | `query`, `tags`, `limit`, `include_superseded`, `entry_type`, `part_of` |
| `list` | Browse entries sorted by title, filterable by tags, exact `entry_type`, and/or `part_of` | `tags`, `limit`, `include_superseded`, `entry_type`, `part_of` |
| `tags` | List all tags with entry counts | — |
| `forget` | Delete an entry (file and index). Warns when other entries still link to it. | `entry_id` |
| `rebuild` | Rebuild search index from Markdown files; also runs `doctor` | — |
| `doctor` | Check every entry against `schema.json`: dangling/superseded `kb://` targets, undeclared types, missing required frontmatter and template body fields, supernodes, tag/type collisions | — |

### Entry schema

The entry taxonomy and its per-type rules live in `schema.json`, not in Python: which frontmatter fields a type requires, which template body fields it should carry, whether search may boost it by usage, and whether `recall` digests its back-links. `entry_type` is exposed to the MCP client as an enum built from that schema, so an undeclared type is rejected before the call reaches the server.

Resolution order, first found wins: `<data-path>/schema.json`, then the packaged `src/schema.json`. A user file **replaces** the packaged one outright — the two are never merged, so copy the default and edit it. The schema is read once at startup, so editing it requires restarting the server. Entries written by hand can still carry an undeclared type; `doctor` reports those.

## Dashboard

A web UI for visually exploring and hand-editing the same knowledge base the MCP tools use — no protocol duplication, every action goes through `KnowledgeBase`.

- **Force-directed graph** of all entries and their `kb://` relations — click a node to inspect it, click a legend chip to filter by type (matches stay lit, others dim)
- **Hybrid search** over the same BM25 + semantic index as the `search` MCP tool, with tag and `entry_type` filters
- **CRUD panel** to create, edit, supersede, or delete entries without touching Markdown files by hand — outgoing/incoming graph relations shown alongside the fields
- Dark, dense, no-chrome interface — a maintenance tool, not a consumer app (see `PRODUCT.md`/`DESIGN.md`)

Disabled by default (the container only runs the MCP server). Enable it as a second process in the same container:

```bash
docker run -d --name engram \
  -e ENGRAM_ENABLE_DASHBOARD=1 \
  -p 8192 -p 8193 \
  -v ./knowledge:/knowledge \
  foreigndmitryi/engram --transport sse

docker port engram 8193   # dashboard host port
```

Or run it standalone locally: `python -m dashboard` (see [Configuration](#configuration) for `ENGRAM_DASHBOARD_HOST`/`ENGRAM_DASHBOARD_PORT`).

## Graph Relations

Link entries with `kb://uuid#type` URLs in Markdown content:

```markdown
This service runs on [Saturn](kb://a1b2c3d4-...#runs-on)
and depends on [PostgreSQL](kb://f9e8d7c6-...#depends-on).
```

`recall` returns both directions, plus size metadata:

```json
{
  "id": "a1b2c3d4-...",
  "title": "My API Service",
  "content": "...",
  "tags": ["..."],
  "size": 1024,
  "last_modified": "2026-03-14",
  "relations": {
    "out": [{"type": "runs-on", "id": "e5f6...", "title": "Saturn"}],
    "in": [{"type": "depends-on", "id": "b7c8...", "title": "Frontend App"}]
  }
}
```

Like [HATEOAS](https://en.wikipedia.org/wiki/HATEOAS) for knowledge — every response carries the links to navigate the graph.

## Usage Examples

### Store knowledge

Ask your agent:

> "Remember that our API runs on port 8080 and depends on PostgreSQL 15."

Engram creates a Markdown file with a unique UUID, indexes it, and confirms. The agent can now recall this fact in any future session.

### Search

> "What do we know about PostgreSQL?"

Engram searches across all entries by content, title, and tags. Results are ranked by relevance.

### Navigate the graph

> "What depends on PostgreSQL?"

If entries link to the PostgreSQL article with `kb://uuid#depends-on`, Engram returns all backlinks — showing every service that depends on it, without the agent having to search for each one.

### Share knowledge across agents

Start Engram with SSE or HTTP transport. Multiple agents — even from different providers (Claude, ChatGPT, Copilot) — connect to the same server. What one agent remembers, all others can recall.

```
Agent A: "Remember that the deploy key rotates every 90 days."
Agent B: "When does the deploy key expire?"
→ Agent B finds the answer immediately.
```

## Prompt Your Agent

Add this to your system prompt or project instructions to make your agent use Engram as a reflex, not an afterthought:

```
Engram is your persistent memory. Using it is mandatory, not optional.

Engram stores ZERO discoverable information. If you can derive it from
code, git history, configuration files, or existing documentation, it
does not belong in Engram. Engram captures decisions and their context,
diagnostics and their root causes, procedures learned the hard way —
the kind of knowledge that is lost when a conversation ends.

Before working on any topic: search Engram first. Always. Even if you think you know.
Before answering a question about infrastructure or architecture: search first.
Before proposing a solution: check if a past decision exists in Engram.

After resolving a diagnostic: remember the root cause and the fix.
After executing a procedure: remember the steps.
After making an architecture decision: remember the choice and the rationale.
After discovering something about the infrastructure: remember it.
```

A system prompt is easy to forget mid-session. [`plugins/engram-hooks/`](plugins/engram-hooks/) ships a self-contained plugin (`SessionStart`, `Stop`, `SessionEnd`, `PreToolUse` handlers, plus a Claude-Code-only `engram-project-onboarder` agent) that mechanically nudges the agent to search Engram before starting work and reminds it to `remember` non-trivial changes before finishing, instead of relying on it recalling this section unprompted. Works in both Claude Code and Codex — install with `/plugin marketplace add <this-repo>` then `/plugin install engram-hooks@engram-memory` (Claude Code), or `codex plugin marketplace add <this-repo>/plugins` then `codex plugin add engram-hooks@engram-memory` (Codex) — see [`plugins/engram-hooks/README.md`](plugins/engram-hooks/README.md) for what each hook does and the full install flow.

### Disable Claude Code's built-in auto memory

Claude Code ships its own automatic memory (`MEMORY.md` under `~/.claude/projects/<project>/memory/`, loaded every session). Running it alongside Engram means two systems writing overlapping notes and competing for the agent's attention, which gets in the way more than it helps. Turn it off in `settings.json`:

```json
{
  "autoMemoryEnabled": false
}
```

Or via environment variable (takes precedence over the setting and the `/memory` toggle): `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. See [Claude Code's memory docs](https://code.claude.com/docs/en/memory#enable-or-disable-auto-memory) for details.

## Configuration

All options have `ENGRAM_*` environment variable fallbacks. CLI args take priority.

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--data-path` | `ENGRAM_DATA_PATH` | `/knowledge` | Root path for knowledge data |
| `--transport` | `ENGRAM_TRANSPORT` | `stdio` | MCP transport |
| `--host` | `ENGRAM_HOST` | `0.0.0.0` | Listen address (SSE/HTTP) |
| `--port` | `ENGRAM_PORT` | `8192` | Listen port (SSE/HTTP) |
| `--embedding-model` | `ENGRAM_EMBEDDING_MODEL` | `minishlab/potion-multilingual-128M` | Model2Vec model for semantic search |
| — | `ENGRAM_ENABLE_DASHBOARD` | unset (off) | Truthy (`1`/`true`/`yes`) starts the dashboard as a second process alongside the MCP server |
| `--host` (dashboard) | `ENGRAM_DASHBOARD_HOST` | `0.0.0.0` | Dashboard listen address |
| `--port` (dashboard) | `ENGRAM_DASHBOARD_PORT` | `8193` | Dashboard listen port (first free port at or above this is used) |
| — | `ENGRAM_QUERY_LOG` | unset (off) | File path; when set, `search`/`recall` append a JSONL trace of each call for retrieval-quality analysis |

## Storage Format

Entries are Markdown files with YAML frontmatter in `<data-path>/entries/`:

```yaml
---
id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
title: Entry Title
tags: [infrastructure, postgresql]
type: decision
resource: /path/to/relevant/file
---

Markdown content here...
```

`type` is required on every `remember` call (a dedicated field, not part of `tags`) — it classifies the entry (`hub`, `decision`, `diagnostic`, `procedure`, `preference`, `snippet`, ...) and is filterable via `search`/`list`. `resource` is optional: a canonical file/folder path the entry describes. Legacy entries written before these fields existed still read fine.

The search index is a rebuildable cache at `<data-path>/index/engram.db`. Delete it and `rebuild` — no data is ever lost. `rebuild` also reports schema-conformance warnings (missing `type`, malformed `resource`) across existing entries.

## Development

```bash
# Build
docker build -t engram .

# Test (separate Dockerfile — pytest/tests/ never ship in the production image)
docker build -f tests/Dockerfile -t engram-test .
docker run --rm engram-test

# Run locally (SSE)
docker run -d --name engram -p 8192 -v ./knowledge:/knowledge engram --transport sse
```

## License

[MIT](LICENSE)
