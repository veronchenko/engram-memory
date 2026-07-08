# Changelog

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
- Tests: 5 new tests covering the new structural and size checks

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
