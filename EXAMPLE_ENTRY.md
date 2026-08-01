---
id: 3f9c2e1a-7b44-4c3a-9e2d-1a2b3c4d5e6f
title: Example Entry — demonstrates the entry file format
tags:
- snippet
- engram_memory
type: snippet
resource: kb://3f9c2e1a-7b44-4c3a-9e2d-1a2b3c4d5e6f
---

Example entry body content (the file body is plain Markdown, written however you like, but the project convention is a short summary up front).

### File format
- File name: `<id>.md`, where `<id>` matches the frontmatter `id` (UUID v4).
- Frontmatter is YAML in flow-style (`default_flow_style=True`), one line between `---`.
- Required fields: `id`, `title`, `tags`, `type`.
- Optional field (only written when non-empty — `_write_entry` omits it otherwise): `resource`.

### Constraints
- `type` is required on every `remember()` call — no entry is written without it (`{"error": "entry_type is required"}`), and it's always present in the file, unconditionally.
- `id` must be a strictly lowercase UUID (`8-4-4-4-12` hex) — checked against the `_UUID_RE` regex, otherwise the path is considered unsafe and rejected (path traversal protection).
- `resource`, if set, must look like a URI (contains `"://"`) or an absolute path (`/...`) — otherwise `rebuild()` flags it in `warnings.malformed_resource`.
- One entry = one fact/decision (atomicity, since v0.6.0) — don't dump several unrelated facts into one body.

### Links (graph relations)
Links of the form `[label](kb://<uuid>#<type>)` in the body are graph edges. Without `#type`, the relation type defaults to `related`.

Example: related to [Engram — Persistent Knowledge Base MCP Server](kb://b39a46a0-65ea-4bda-a4d4-3ebe395bfccd#hub).

### Writing to disk
Written atomically: first to a temporary `*.md.tmp`, then `rename()`d to the target `<id>.md` — so a failed write (disk full, no permissions) never leaves a corrupt file.
