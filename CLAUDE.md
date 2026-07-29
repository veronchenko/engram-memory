# CLAUDE.md — engram_memory

MCP server providing persistent memory for AI agents. Markdown files are the source of truth; a hybrid SQLite FTS5 + semantic embedding index (fused via Reciprocal Rank Fusion) provides search, plus graph-style relations (`kb://uuid#type` links).

## Layout
- `src/server.py` — MCP tool definitions (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`, `doctor`)
- `src/schema.json` + `src/schema.py` — the entry taxonomy as data: per-type `required`/`body`/`membership`/`usage_boost`/`digest_on_recall`. Loader resolves `<data-path>/schema.json` → packaged default, **full replacement, never a merge**; builds the `entry_type` enum injected into `server.py`'s module globals before tool registration (PEP 563 annotations resolve there), so an undeclared type is rejected client-side. Read once at startup → editing it requires a restart.
- `src/doctor.py` — schema-driven integrity pass (`run_doctor`), shared by the `doctor` tool, `rebuild`'s warnings, and `remember`'s conformance warnings via `check_entry`. Reads the Markdown files, not the index.
- `src/database.py` — entry storage: Markdown + YAML frontmatter CRUD, UUID assignment
- `src/search_backend.py` — `SQLiteBackend` (`index`, `unindex`, `search`, `rebuild`, `get_relations`, `get_all_relations`) + `kb://` relation extraction. `search` fuses BM25 (FTS5) with cosine similarity over local Model2Vec embeddings (`_bm25_search` + `_vector_search` + `_rrf_fuse`); embeddings are stored as a BLOB column on `entries` and computed lazily/in batch by the same code path that writes the row.
- `src/dashboard/` — web dashboard: FastAPI app (`app.py`, `create_app(kb)`) exposing REST CRUD + `/api/graph` over the same `KnowledgeBase`/`SQLiteBackend` the MCP tools use (no protocol duplication), a single static `index.html` (vanilla JS force-directed canvas graph, no build step/CDN) served at `/`, and `__main__.py` as its own `uvicorn` entry point (`ENGRAM_DASHBOARD_HOST`/`ENGRAM_DASHBOARD_PORT`, default port 8193). Runs as a second process in the same container via `docker-entrypoint.sh`, alongside `server.py`'s MCP process.
- `tests/` — mirrors `src/` (`test_server.py`, `test_database.py`, `test_backends.py`, `test_dashboard.py`)

## Conventions
- Entries: Markdown file per entry in `<data-path>/entries/`, YAML frontmatter (`id`, `title`, `tags`), UUID-named.
- Bi-temporal fields (optional frontmatter, omitted when empty): `valid_at` (ISO8601 UTC, set automatically on creation/versioning), `superseded_by`/`supersedes` (UUIDs linking an old and new version). `remember(..., supersede=True)` on an update creates a new entry instead of overwriting in place, marking the old one `superseded_by` the new id. `search`/`list` hide superseded entries by default (`include_superseded=True` to see history); `recall` on an old id still returns that version's own content plus `superseded_by`.
- Index is a rebuildable cache at `<data-path>/index/engram.db` — never treat it as source of truth; `rebuild` regenerates it from entries. Exception: usage counters (`access_count`/`last_accessed`) and the content-addressed embedding cache (`content_hash`) live only in the index and are deliberately carried across `rebuild`.
- Memory hygiene: `remember` rejects semantic near-duplicates of live entries at cosine ≥ `WRITE_GATE_MIN_SIMILARITY` (0.90) unless `force`/`supersede`; `search` rescores hits by usage (`final = rrf × log-boost × exp-decay`, half-life 69 days, lazy — no cron; only `recall` bumps counters — search hits deliberately don't, since counting them feeds the boost back into its own ranking); `recall` caps relations per direction (`relations_limit`, default 20) with `relations_truncated`; `kb://uuid#type:edge` links carry edge semantics (`supports`/`contradicts`/`related_to`).
- Type-driven behaviour is never `if type == "hub"` in code — it is a field in `schema.json` (`usage_boost: false` exempts hubs from the search usage boost, `digest_on_recall: true` makes `recall` return `in_digest` instead of a truncated `in` list). Same for the `kb://` edge vocabulary: `schema.json`'s `edges` is what `extract_relations` validates against — `KnowledgeBase` pushes it onto the backend (`allowed_edges`) exactly as it does `no_boost_types`, so a hand-built backend can't silently diverge. The digest is opt-in per call (`kb.get(..., digest=True)`), set by `recall` and deliberately not by the dashboard, which renders the flat list.
- `part_of` — structural membership (distinct from a semantic `kb://` link) from a detail entry (`decision`/`diagnostic`/`feature`/`procedure`/`integration`, optional for `pattern`/`snippet`/`idea`) to one or more hub UUIDs; enforced per type via the schema's `membership` field (`required`/`optional`/`none`), filterable on `search`/`list`, and grouped alongside `kb://` back-links in a hub's `recall` digest.
- Config via CLI args or `ENGRAM_*` env vars (CLI wins). `ENGRAM_QUERY_LOG` (a file path, unset by default) makes `search`/`recall` append a JSONL trace (query/returned ids, or entry id/found) via `log_query()` in `server.py`, for `scripts/eval_retrieval.py`'s ground truth; write failures never break the calling tool.
- Embedding model: `minishlab/potion-multilingual-128M` (Model2Vec, 256-dim), overridable via `--embedding-model`/`ENGRAM_EMBEDDING_MODEL`. Pre-downloaded into the Docker image (`HF_HOME=/app/.cache/huggingface`) so runtime and tests never need network access for it. No pluggable backend abstraction — the project deliberately reverted that (see Engram diagnostic `dac9034d`); hybrid search lives directly in `SQLiteBackend`.

## Testing
Tests run in a separate Dockerfile (`tests/Dockerfile`, builds `FROM engram:latest` — not a stage in the main `Dockerfile`, so a plain `docker build .` always produces production, never the test image):
```bash
docker build -t engram .
docker build -f tests/Dockerfile -t engram-test .
docker run --rm engram-test
```
Retrieval quality is measured with `scripts/eval_retrieval.py` (golden query set lives outside the repo, e.g. `~/.claude/engram/golden.yaml`; run against a KB copy, in Docker via `--entrypoint python -v <repo>:/repo`).

Also sidesteps a Windows-only bug (`os.rename` refusing to overwrite an existing file — see Gotchas) that only reproduces on native Windows, not in the Linux container.

## Gotchas
- Windows: `os.rename` fails when overwriting an existing entry file — use an OS-safe replace, not raw `rename` (see git history / Engram diagnostic entry `7c31e93a`).
- Atomicity: one decision per article — don't cram multiple unrelated facts into a single entry (enforced since v0.6.0).

Extra docs:
 DESIGN.md and PRODUCT.md - for design tasks and ui updates.