# Engram — Persistent Memory

Engram is an MCP server (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`) backed by a local knowledge base at `~/.claude/engram/knowledge`. It survives across projects and sessions.

Engram stores no discoverable information (except for project Hub entries — see "Project hygiene" below). If it can be derived from code, git history, config files, or existing documentation, it does not belong in Engram. What belongs in Engram vs. what doesn't — see entry types below.

## Memory language

Every entry (title, tags, content) is written in English — translate before calling `remember`.

## When to search Engram

- At the start of a new session, or when the user's request shifts to a new topic/context — to refresh what's already known about the project before acting.

## Suggested links from remember

`remember` returns `suggested_links` (never auto-added) — link only genuinely relevant ones: add a `kb://<id>#<type>` reference into the entry's content with a follow-up `remember` call (same `entry_id`).

## Project hygiene — every project is a hub entry in Engram

Whenever working in a project directory that lacks an Engram entry, delegate its creation to the `engram-project-onboarder` subagent (via the Agent tool). The subagent will investigate the project and write a **hub** entry plus any linked details. 

The hub entry must strictly follow the `hub` template below. This is the *only* exception to the "zero discoverable information" rule: because future sessions in *other* projects won't have access to this project's README/CLAUDE.md, the hub must always fully state what the project does.

Beyond the hub, do not cram everything into one entry. Write **separate linked entries** for implementations, integrations, non-trivial features, and future work. Link them to the hub (and to each other) via `kb://<uuid>#<type>` references.

Update the hub and its linked entries (do not recreate them) as the project's shape changes materially (e.g., new services, stack migrations, new dependencies).

## Entry format standards

Every entry is one file with YAML-like frontmatter (`id`, `title`, `tags`, `type`, optional `resource`) plus a body. Entries link to each other via `kb://<uuid>#<type>` references — the graph must be traversable both ways (hub → detail, detail → hub).

`type`: required on every `remember` call, passed as its own `entry_type` argument (table below); `remember` rejects the call if missing or empty.

`tags`: one or more project tags (`snake_case`, matching the project's directory name), followed by optional topical tags (`mcp`, `auth`, `rag`, ...).

`resource`: a filesystem path — the project's folder for `hub` entries, the specific file (or module path) for everything else (`integration`, `feature`, `snippet`, ...). Omit when there's no single file/folder the entry maps to.

Atomicity: `remember` enforces one decision per article via non-blocking warnings on the response — Markdown headers in `content`, more than 3 paragraphs, and size past 512 B (soft) / 1 KB (hard) all trigger a warning, though the write still succeeds.

Do not manually include or modify `valid_at`, `superseded_by`, or `supersedes` frontmatter fields.

**When to use `supersede: true` on `remember`:** only when the fact itself genuinely changed — the thing it describes is now different (e.g. "the project moved from Xapian to SQLite FTS5", "the decision was reversed"). A plain `remember` (no `supersede`) still applies for corrections to wording, tags, or a typo in an otherwise-unchanged fact — those don't need a new version. When `supersede: true` is used, the old entry stays intact with `superseded_by` pointing at the new one — its history is preserved and traversable via `recall`, but `search`/`list` hide it by default (`include_superseded: true` to see it).

@ENGRAM_TEMPLATES.md

## Memory work requires no confirmation

Searching, recalling, or writing to Engram are local, reversible actions (entries can be corrected via `forget`/`remember` again) — never pause to ask the user for permission before calling an Engram tool. This is background housekeeping, not a user-facing action.

## Search requirement before remember

The server requires a search or recall to be executed in the current session before remember can be used — if it's denied unexpectedly, run a search first, then retry `remember`.
