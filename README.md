# Engram — Persistent Knowledge Base MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents persistent memory. Markdown files as source of truth, hybrid keyword + semantic search, typed graph relations.

**Website:** [engram-kb.org](https://engram-kb.org) · **Docker Hub:** [foreigndmitryi/engram](https://hub.docker.com/r/foreigndmitryi/engram)

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

## Search

Hybrid: SQLite FTS5 (Porter stemming, BM25) fused with cosine similarity over local [Model2Vec](https://github.com/MinishLab/model2vec) embeddings (`minishlab/potion-multilingual-128M`, no cloud dependency) via Reciprocal Rank Fusion — so a query that shares no literal words with an entry can still find it by meaning. Falls back to keyword-only if the embedding model can't load. The index is still a single SQLite file you can query with standard SQL tools.

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `remember` | Create or update an entry (upsert with duplicate detection, or version it via `supersede`). `entry_type` is required. Returns `size`, non-blocking atomicity `warnings` (Markdown headers, >3 paragraphs, >512 B / >1 KB), and `suggested_links` — near-duplicate/related entries (by embedding similarity) worth cross-referencing with a `kb://` link. | `title`, `content`, `tags`, `entry_type`, `entry_id`, `force`, `resource`, `supersede` |
| `recall` | Read an entry with its graph relations (outgoing + backlinks). Returns `size` and `last_modified`. | `entry_id` |
| `search` | Hybrid keyword + semantic search, filterable by tags and/or exact `entry_type` | `query`, `tags`, `limit`, `include_superseded`, `entry_type` |
| `list` | Browse entries sorted by title | `tags`, `limit` |
| `tags` | List all tags with entry counts | — |
| `forget` | Delete an entry (file and index) | `entry_id` |
| `rebuild` | Rebuild search index from Markdown files | — |

## Dashboard

A web UI for visually exploring and hand-editing the same knowledge base the MCP tools use — no protocol duplication, every action goes through `KnowledgeBase`.

![Engram dashboard — force-directed graph of entries and their kb:// relations, colored by type](docs/screenshots/dashboard-graph.png)

- **Force-directed graph** of all entries and their `kb://` relations — click a node to inspect it, click a legend chip to filter by type (matches stay lit, others dim)
- **Hybrid search** over the same BM25 + semantic index as the `search` MCP tool, with tag and `entry_type` filters
- **CRUD panel** to create, edit, supersede, or delete entries without touching Markdown files by hand — outgoing/incoming graph relations shown alongside the fields

![Engram dashboard — entry selected, showing the edit panel with title/type/tags/content and its graph relations](docs/screenshots/dashboard-entry-panel.png)

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

A system prompt is easy to forget mid-session. For Claude Code, [`hooks/`](hooks/) ships a self-contained plugin (`SessionStart`, `Stop`, `SessionEnd` handlers) that mechanically nudges the agent to search Engram before starting work and reminds it to `remember` non-trivial changes before finishing, instead of relying on it recalling this section unprompted. See [`hooks/README.md`](hooks/README.md) for what each hook does and how to wire it into `settings.json`.

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

# Test (90 tests, separate Dockerfile — pytest/tests/ never ship in the production image)
docker build -f tests/Dockerfile -t engram-test .
docker run --rm engram-test

# Run locally (SSE)
docker run -d --name engram -p 8192 -v ./knowledge:/knowledge engram --transport sse
```

## License

[MIT](LICENSE)
