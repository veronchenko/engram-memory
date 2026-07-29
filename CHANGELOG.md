# Changelog

## 0.12.0

- feat: `recall` gains `hops=2` — walk one more level of graph relations in the same direction, tagging hop-2 items with `via` (the hop-1 id they were reached through) instead of resolving a title, since a hop-2 item is a navigation breadcrumb, not read content
- feat: `search` fuses a third RRF channel — `_exact_match_search`, an IDF-weighted literal title/tag token match. BM25 and embeddings both dilute a proper noun among lexically-similar distractors on a larger corpus; this channel recovers it by literal substring match, weighted so a rare token counts more than one shared by dozens of entries, and abstains below `MIN_EXACT_DISCRIMINATING_TOKENS` matching tokens rather than let SQL row order decide ties. MRR improved 0.851 → 0.857 and recall@5 improved 0.851 → 0.869, with no regression on any language slice
- feat: `search` results drop the volatile `access_count`/`last_accessed`/`staleness` fields — display-only, drift with wall-clock time on every call — so identical repeated searches stay byte-identical for prompt-cache reuse; still exposed via `recall`/`doctor`
- refactor: `ENGRAM_*` CLI/env resolution (duplicated between `server.py` and `dashboard/__main__.py`) and tuning constants (write-gate/staleness thresholds, RRF/exact-match knobs, pagination/duplicate-detection defaults — previously scattered across `search_backend.py`/`database.py`, including a cross-import between them just to share two of them) consolidated into `src/config.py`. No behavior change — same env var names, same defaults
- docs: README documents the exact-match channel and `hops=2`, and adds a comparison against the same content packaged as a conventional Markdown wiki — Engram answers with fewer tool calls, less total context, and less wall time for equivalent fact coverage

## 0.11.1

- fix: dashboard `/api/graph` now includes `part_of` membership as edges — after the `part_of` migration (kb:// hub back-links replaced by the structural frontmatter field), members whose only connection was a stripped back-link rendered as isolated nodes. `KnowledgeBase.get_graph()` adds a `part_of`/`part_of`-typed edge per membership, skipping any hub already reachable via a kept `kb://` link (e.g. `pattern` entries)

## 0.11.0

- feat: entry taxonomy moves into `schema.json` — per-type rules (required frontmatter, template body fields, `part_of` membership, usage-boost/digest behavior) are data, not Python. A generated `entry_type` enum enforces it client-side on `remember`, and a new `doctor` MCP tool audits every entry against it (dangling/superseded `kb://` links, undeclared types, missing fields, supernodes, tag/type collisions). `part_of` adds structural membership from a detail entry to its hub, filterable on `search`/`list` and grouped in a hub's `recall` digest
- feat: BM25 now retries with OR when an implicit-AND match returns nothing; retrieval-ranking quality (hit@k/recall@k/MRR) is now tracked internally, and optional `ENGRAM_QUERY_LOG` traces `search`/`recall` calls to JSONL to support it
- fix: hub entries no longer skew search ranking from their own read count; the schema's `edges` list is now actually enforced; the write gate checks 20 candidates instead of 4; a tz-naive `last_accessed` no longer crashes `search`; `resource` survives an update that omits it; search hits no longer inflate `access_count` (only `recall` does)
- **Breaking:** `rebuild`'s `schema_warnings` kinds now come from the `doctor` report (`malformed_resource` dropped, `missing_type` → `type_outside_schema`)

## 0.10.0

- feat: web dashboard (`src/dashboard/`) — FastAPI REST CRUD + `/api/graph` over the same `KnowledgeBase`/`SQLiteBackend` the MCP tools use, served with a single static `index.html` (vanilla JS force-directed canvas graph, no build step/CDN); disabled by default, enabled via `ENGRAM_ENABLE_DASHBOARD`, runs as a second process alongside `server.py` in the same container (`docker-entrypoint.sh`)
- feat: bi-temporal entry versioning — `remember(..., supersede=True)` creates a new version instead of overwriting in place; `search`/`list` hide superseded entries by default (`include_superseded=True` to see history)
- feat: `search` gains an `entry_type` filter (exact match), also exposed on the dashboard's `/api/search`
- feat: `remember` suggests `kb://` links — returns `suggested_links` (near-duplicate/related entries by embedding similarity) for the caller to cross-reference, never auto-added
- feat: Claude Code hooks plugin (`hooks/`) — `SessionStart`/`Stop`/`SessionEnd` handlers that nudge the agent to search Engram before starting work and to `remember` non-trivial changes before finishing; includes a `PreToolUse` gate requiring a search/recall before `remember` in the same session
- fix: Windows `os.rename` failing when overwriting an existing entry file — replaced with an OS-safe replace
- fix: container now exits when either the MCP backend or the dashboard process dies, instead of hanging
- fix: backgrounding the MCP backend behind the dashboard process preserved real stdin so a stdio MCP client's input still reaches it

## 0.9.0

- feat: `search` is now hybrid — SQLite FTS5 (BM25) fused with cosine similarity over local Model2Vec embeddings (`minishlab/potion-multilingual-128M`) via Reciprocal Rank Fusion, so queries that share no literal words with an entry can still find it by meaning
- Entries store an `embedding` BLOB column (migrated in place on existing indexes); computed on `remember` and batch-computed on `rebuild`
- New `--embedding-model`/`ENGRAM_EMBEDDING_MODEL` option (default `minishlab/potion-multilingual-128M`)
- Degrades gracefully to keyword-only search if the embedding model can't load (no network on first run)
- `Dockerfile` gains a dedicated build stage so the production image only ships runtime code; also pre-downloads the embedding model so it works with no network access at runtime

## 0.8.0

- **Breaking:** removed the pluggable backend abstraction and the Xapian backend — SQLite FTS5 (Porter stemming, BM25 ranking) is now the only search backend
- **Breaking:** removed `--backend`/`ENGRAM_BACKEND` and `--language`/`ENGRAM_LANGUAGE` CLI/env options
- **Breaking:** default index path changed from `<data-path>/index/<backend>/` to `<data-path>/index/engram.db` — run `rebuild` after upgrading to reindex
- `src/backend/` (both `xapian/` and `sqlite/` subpackages plus the `SearchBackend` ABC) replaced by a single `src/search_backend.py` module
- Reason: two backends doing the same job (keyword full-text search) didn't justify an `importlib`-based plugin system; simplifies the codebase ahead of adding real search-strategy diversity (e.g. a hybrid semantic backend)

## 0.7.0

- feat: entries support optional `type`, `resource` frontmatter fields
- feat: `remember` accepts `entry_type`, `resource` params to set them
- feat: `search`/`list`/`recall` surface `type` in results
- feat: `type` is filterable in the Xapian backend (`type:` prefix); indexed as a boolean term in both backends
- feat: `rebuild` now returns schema conformance warnings (missing type, malformed resource)
- Backward compatible: existing entries without new fields continue to work unchanged

## 0.6.0

- feat: `remember` docstring enforces one decision per article with optional justification
- feat: structural warnings added — Markdown headers detection, paragraph count > 3
- feat: size thresholds lowered to 512 B (soft warning) / 1 KB (hard warning)

## 0.5.2

- Docs: reinforced "zero discoverable information" principle in README.md, CLAUDE.md, and `remember` tool docstring (server.py)
- Docs: stripped technical documentation from CLAUDE.md (9256bd7)

## 0.5.1

- Added content policy to `remember` tool docstring (size limits, article structure guidelines)
- Docs: renamed all "KB" references to "Engram" / "knowledge base" across README and CLAUDE.md

## 0.5.0

- `recall` now returns `size` (bytes) and `last_modified` (date) fields
- `remember` now returns `size` (bytes) and a `warnings` list when article content exceeds 2 KB (soft) or 4 KB (hard) thresholds
- Added 4 usage examples to README (store, search, graph, multi-agent)
- Added "Prompt Your Agent" section to README with system prompt template

## 0.4.2

- README rewritten for v0.4.0 (Docker-only development, cleaner transport blocks, Custom Backend section with SearchBackend ABC + Whoosh example)

## 0.4.1

- Removed Whoosh backend (server-side coverage increased, deps simplified)
- Removed `--log-file` option — logs always go to stderr (`docker logs`)
- Added CI coverage report with 80% minimum threshold
- Fixed tool descriptions: "configurable stemming" instead of "French stemming"

## 0.4.0

- Pluggable search backends: `xapian` (default), `sqlite` (FTS5), `whoosh` (pure Python)
- Backend loaded dynamically via `importlib` — any `backend/<name>/main.py` works
- All CLI options have `ENGRAM_*` environment variable fallbacks
- `ENGRAM_*` env vars baked into Docker image as defaults
- Source code moved to `src/` directory

## 0.3.0

- Metadata cache (`_meta_cache`) in `KnowledgeBase` — powers `list`, `tags`, `find_similar`
- Security: path traversal protection (UUID regex), limit clamping, atomic file writes, non-root Docker user
- Best practice guidance added to `remember` tool description
- GitHub mirror excludes `ci/` and `CLAUDE.md`

## 0.2.0

- Renamed project to Engram
- Consolidated tools to 7: remember (upsert), recall, search, list, tags, forget, rebuild
- Graph relations via `kb://uuid#type` links in content (outgoing + incoming backlinks)
- Three transports: stdio, SSE, streamable-http
- Docker support (Alpine image with `cylian/engram`)
- CLI options: `--data-path`, `--log-file`, `--transport`, `--host`, `--port`
- Duplicate detection with title similarity (SequenceMatcher)
- Prepared for open-source release (MIT license, README)

## 0.1.0

- Initial release
- 8 MCP tools: kb_search, kb_get, kb_store, kb_update, kb_delete, kb_list, kb_tags, kb_rebuild
- Markdown files with YAML frontmatter as storage
- Xapian full-text search with French stemming
- Duplicate detection on store
- Tag-based filtering
- stdio transport (Claude Code integration)
