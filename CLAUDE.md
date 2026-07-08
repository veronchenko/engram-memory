# CLAUDE.md — engram_memory

MCP server providing persistent memory for AI agents. Markdown files are the source of truth; a pluggable search backend (Xapian default, SQLite FTS5 alternative) indexes them for full-text search and graph-style relations (`kb://uuid#type` links).

## Layout
- `src/server.py` — MCP tool definitions (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`)
- `src/database.py` — entry storage: Markdown + YAML frontmatter CRUD, UUID assignment
- `src/backend/` — pluggable search backends; each is `backend/<name>/main.py` implementing `SearchBackend` (`index`, `unindex`, `search`, `rebuild`, `get_relations`)
- `tests/` — mirrors `src/` (`test_server.py`, `test_database.py`, `test_backends.py`)

## Conventions
- Entries: Markdown file per entry in `<data-path>/entries/`, YAML frontmatter (`id`, `title`, `tags`), UUID-named.
- Index is a rebuildable cache under `<data-path>/index/<backend>/` — never treat it as source of truth; `rebuild` regenerates it from entries.
- New backend = new class inheriting `SearchBackend`, loaded via `importlib` by `--backend` name — no registry edits needed elsewhere.
- Config via CLI args or `ENGRAM_*` env vars (CLI wins).

## Testing
```bash
docker run --rm engram python -m pytest tests/ -v
```
89 tests, 90% coverage as of last release — keep coverage from regressing on new backends/tools.

## Gotchas
- Windows: `os.rename` fails when overwriting an existing entry file — use an OS-safe replace, not raw `rename` (see git history / Engram diagnostic entry `7c31e93a`).
- Atomicity: one decision per article — don't cram multiple unrelated facts into a single entry (enforced since v0.6.0).
