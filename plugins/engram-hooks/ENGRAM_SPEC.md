<!-- Source of truth: plugins/engram-hooks/ in the engram_memory repo. The ~/.claude copy is installed from here — edit the plugin copy, then re-install. -->

# Engram — Persistent Memory

Engram is an MCP server backed by a local knowledge base at `~/.claude/engram/knowledge`. It survives across projects and sessions.

Engram stores no discoverable information (one exception: hubs — see "Project hygiene" below). If it can be derived from code, git history, config files, or existing documentation, it does not belong in Engram.

## Memory language

The KB is English-only: translate both entry content (before `remember`) and queries (before `search`).

## When to search Engram

- Before the first substantive action on every user request, and again when the request shifts to a new topic/context.

## Suggested links from remember

`remember` returns `suggested_links` (never auto-added) — link only genuinely relevant ones: add a `kb://<id>#<type>` reference into the entry's content with a follow-up `remember` call (same `entry_id`).

## Project hygiene — every project is a hub entry in Engram

Whenever working in a project directory that lacks an Engram entry, delegate its creation to the `engram-project-onboarder` subagent (via the Agent tool).

The hub entry must follow the `hub` template below. This is the *only* exception to the "zero discoverable information" rule: because future sessions in *other* projects won't have access to this project's README/CLAUDE.md, the hub must always fully state what the project does.

Beyond the hub, do not cram everything into one entry. Write **separate entries** for implementations, integrations, non-trivial features, and future work, attached to the hub via `part_of` (see below).

Update the hub and its linked entries (do not recreate them) as the project's shape changes materially (e.g., new services, stack migrations, new dependencies).

## Entry format standards

Every entry is one file with YAML-like frontmatter (`id`, `title`, `tags`, `type`, optional `resource`, `part_of`) plus a body. Entries link to each other via `kb://<uuid>#<type>` references for semantic relationships (supports/contradicts/related_to) — never for membership.

`type`: passed as the `entry_type` argument to `remember` — pick from the table below.

`part_of`: list of hub UUIDs the entry belongs to, passed as the `part_of` argument to `remember`. Per-type requirement is in the table below; a required-but-missing `part_of` rejects the call — find the hub via `search`/`list`, or create the hub first. Filterable in `search` and `list` (`part_of=[<hub uuid>]`).

`tags`: topical tags only (`mcp`, `auth`, `rag`, ...). Do NOT add a project tag — project scoping is `part_of`'s job.

`resource`: filesystem path — the project folder for `hub`, the module path for `integration` (both required); optional elsewhere, set only when the entry maps to a single file/folder.

Atomicity: one decision per article — no Markdown headers, ≤3 paragraphs, target ≤512 B. `remember` warns (non-blocking) on violations.

**When to use `supersede: true` on `remember`:** only when the fact itself genuinely changed — the thing it describes is now different (e.g. "the project moved from Xapian to SQLite FTS5", "the decision was reversed"). A plain `remember` (no `supersede`) still applies for corrections to wording, tags, or a typo in an otherwise-unchanged fact — those don't need a new version.

@ENGRAM_TEMPLATES.md

## Memory work requires no confirmation

Searching, recalling, or writing to Engram are local, reversible actions (entries can be corrected via `forget`/`remember` again) — never pause to ask the user for permission before calling an Engram tool.

## Search requirement before remember

A PreToolUse hook denies `remember` until a `search`/`recall` has run this session — if `remember` is denied, search first and retry.
