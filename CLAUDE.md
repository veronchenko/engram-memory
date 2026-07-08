# CLAUDE.md — engram_memory

MCP server providing persistent memory for AI agents. Markdown files are the source of truth; a hybrid SQLite FTS5 + semantic embedding index (fused via Reciprocal Rank Fusion) provides search, plus graph-style relations (`kb://uuid#type` links).

## Layout
- `src/server.py` — MCP tool definitions (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`)
- `src/database.py` — entry storage: Markdown + YAML frontmatter CRUD, UUID assignment
- `src/search_backend.py` — `SQLiteBackend` (`index`, `unindex`, `search`, `rebuild`, `get_relations`, `get_all_relations`) + `kb://` relation extraction. `search` fuses BM25 (FTS5) with cosine similarity over local Model2Vec embeddings (`_bm25_search` + `_vector_search` + `_rrf_fuse`); embeddings are stored as a BLOB column on `entries` and computed lazily/in batch by the same code path that writes the row.
- `src/dashboard/` — web dashboard: FastAPI app (`app.py`, `create_app(kb)`) exposing REST CRUD + `/api/graph` over the same `KnowledgeBase`/`SQLiteBackend` the MCP tools use (no protocol duplication), a single static `index.html` (vanilla JS force-directed canvas graph, no build step/CDN) served at `/`, and `__main__.py` as its own `uvicorn` entry point (`ENGRAM_DASHBOARD_HOST`/`ENGRAM_DASHBOARD_PORT`, default port 8193). Runs as a second process in the same container via `docker-entrypoint.sh`, alongside `server.py`'s MCP process.
- `tests/` — mirrors `src/` (`test_server.py`, `test_database.py`, `test_backends.py`, `test_dashboard.py`)

## Conventions
- Entries: Markdown file per entry in `<data-path>/entries/`, YAML frontmatter (`id`, `title`, `tags`), UUID-named.
- Bi-temporal fields (optional frontmatter, omitted when empty): `valid_at` (ISO8601 UTC, set automatically on creation/versioning), `superseded_by`/`supersedes` (UUIDs linking an old and new version). `remember(..., supersede=True)` on an update creates a new entry instead of overwriting in place, marking the old one `superseded_by` the new id. `search`/`list` hide superseded entries by default (`include_superseded=True` to see history); `recall` on an old id still returns that version's own content plus `superseded_by`.
- Index is a rebuildable cache at `<data-path>/index/engram.db` — never treat it as source of truth; `rebuild` regenerates it from entries.
- Config via CLI args or `ENGRAM_*` env vars (CLI wins).
- Embedding model: `minishlab/potion-multilingual-128M` (Model2Vec, 256-dim), overridable via `--embedding-model`/`ENGRAM_EMBEDDING_MODEL`. Pre-downloaded into the Docker image (`HF_HOME=/app/.cache/huggingface`) so runtime and tests never need network access for it. No pluggable backend abstraction — the project deliberately reverted that (see Engram diagnostic `dac9034d`); hybrid search lives directly in `SQLiteBackend`.

## Testing
Tests run in a separate Dockerfile (`tests/Dockerfile`, builds `FROM engram:latest` — not a stage in the main `Dockerfile`, so a plain `docker build .` always produces production, never the test image):
```bash
docker build -t engram .
docker build -f tests/Dockerfile -t engram-test .
docker run --rm engram-test
```
Also sidesteps a Windows-only bug (`os.rename` refusing to overwrite an existing file — see Gotchas) that only reproduces on native Windows, not in the Linux container.

## Gotchas
- Windows: `os.rename` fails when overwriting an existing entry file — use an OS-safe replace, not raw `rename` (see git history / Engram diagnostic entry `7c31e93a`).
- Atomicity: one decision per article — don't cram multiple unrelated facts into a single entry (enforced since v0.6.0).
