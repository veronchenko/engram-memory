# Engram — Persistent Knowledge Base MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents persistent memory. Markdown files as source of truth, pluggable search backends, typed graph relations.

**Website:** [engram-kb.org](https://engram-kb.org) · **Docker Hub:** [cylian/engram](https://hub.docker.com/r/cylian/engram)

## Quick Start

### stdio

Your agent manages the server. Recommended for Claude Code, ChatGPT Desktop, Cursor.

```bash
claude mcp add --transport stdio engram -- \
  docker run -i --rm -v ./knowledge:/knowledge cylian/engram
```

### SSE

Persistent server on the network. Share knowledge across multiple agents.

```bash
docker run -d --name engram \
  -p 8192:8192 \
  -v ./knowledge:/knowledge \
  cylian/engram --transport sse

claude mcp add --transport sse engram http://your-host:8192/sse
```

### HTTP

Stateless, load-balanceable.

```bash
docker run -d --name engram \
  -p 8192:8192 \
  -v ./knowledge:/knowledge \
  cylian/engram --transport streamable-http

claude mcp add --transport http engram http://your-host:8192/mcp
```

## Search Backends

Two backends ship out of the box. Switch with `--backend` or `ENGRAM_BACKEND`:

| Backend | Default | Description |
|---------|---------|-------------|
| `xapian` | ✓ | Fast full-text search with configurable stemming |
| `sqlite` | | SQLite FTS5 — query your index with standard SQL tools |

The backend is pluggable — see [Custom Backend](#custom-backend) below.

## Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `remember` | Create or update an entry (upsert with duplicate detection). `entry_type` is required. Returns `size` and non-blocking atomicity `warnings` (Markdown headers, >3 paragraphs, >512 B / >1 KB). | `title`, `content`, `tags`, `entry_type`, `entry_id`, `force`, `resource` |
| `recall` | Read an entry with its graph relations (outgoing + backlinks). Returns `size` and `last_modified`. | `entry_id` |
| `search` | Full-text search with optional tag filter | `query`, `tags`, `limit` |
| `list` | Browse entries sorted by title | `tags`, `limit` |
| `tags` | List all tags with entry counts | — |
| `forget` | Delete an entry (file and index) | `entry_id` |
| `rebuild` | Rebuild search index from Markdown files | — |

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

## Configuration

All options have `ENGRAM_*` environment variable fallbacks. CLI args take priority.

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--data-path` | `ENGRAM_DATA_PATH` | `/knowledge` | Root path for knowledge data |
| `--backend` | `ENGRAM_BACKEND` | `xapian` | Search backend |
| `--language` | `ENGRAM_LANGUAGE` | `en` | Stemmer language |
| `--transport` | `ENGRAM_TRANSPORT` | `stdio` | MCP transport |
| `--host` | `ENGRAM_HOST` | `0.0.0.0` | Listen address (SSE/HTTP) |
| `--port` | `ENGRAM_PORT` | `8192` | Listen port (SSE/HTTP) |

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

The search index is a rebuildable cache in `<data-path>/index/<backend>/`. Delete it and `rebuild` — no data is ever lost. `rebuild` also reports schema-conformance warnings (missing `type`, malformed `resource`) across existing entries.

## Custom Backend

Create `backend/{name}/main.py` with a class inheriting `SearchBackend`:

```python
from backend import SearchBackend

class MyBackend(SearchBackend):
    def index(self, entry):
        """Index or update an entry (upsert)."""
        ...

    def unindex(self, entry_id):
        """Remove an entry from the index."""
        ...

    def search(self, query_str, tags, limit):
        """Full-text search. Return [{id, score}]."""
        ...

    def rebuild(self, entries):
        """Rebuild index from entries list. Return count."""
        ...

    def get_relations(self, entry_id):
        """Return {out: [{type, id}], in: [{type, id}]}."""
        ...
```

Then use it with `--backend {name}`. Engram loads it automatically via `importlib`.

### Example: adding Whoosh backend

```dockerfile
FROM cylian/engram:latest

# Install Whoosh
RUN pip install --no-cache-dir whoosh==2.7.4

# Add backend
COPY whoosh_backend/ /app/backend/whoosh/
```

```bash
docker build -t engram-whoosh .
docker run -i --rm -v ./knowledge:/knowledge engram-whoosh --backend whoosh
```

## Development

```bash
# Build
docker build -t engram .

# Test (89 tests, 90% coverage)
docker run --rm engram python -m pytest tests/ -v

# Run locally (SSE)
docker run -d --name engram -p 8192:8192 -v ./knowledge:/knowledge engram --transport sse
```

## License

[MIT](LICENSE)
