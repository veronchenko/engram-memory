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

| type | Use for | Back-link to hub? |
|---|---|---|
| `hub` | One per project/service — what it is, where it lives, stack, connections | — (it *is* the anchor) |
| `pattern` | Reusable architectural pattern that originated in one place, applicable elsewhere | link to the origin project as hub |
| `integration` | A client/consumer/adapter implemented for a specific external API, service, broker, or library — so it's found and reused instead of rebuilt | link to the project's hub |
| `feature` | Non-trivial feature inside a project and how it's built | link to the project's hub |
| `decision` | Choice among alternatives without a standalone artifact (not substantial enough for `feature`) | link to the project's hub |
| `diagnostic` | Root cause + fix of a resolved bug/incident | link to the project's hub |
| `procedure` | Steps for a non-obvious procedure (deploy, migration, one-off setup) | link to the project's hub, if applicable |
| `preference` | Dev/tooling/process preference not already covered by a CLAUDE.md | usually no hub link (global scope) |
| `snippet` | Small reusable code/config snippet | link to the project's hub if project-specific |


### Entry templates (strict format)

Generate markup matching the structure and fields of the chosen `type` template.

**1. `hub`**

```markdown
---
id: <uuid>
title: <project> — <short_desc>
type: hub
tags:
- <project>
resource: <absolute_path_to_folder>
---
<1-2 sentences overview>

**What it does:** <core_flows_and_endpoints>
**Stack:** <lang, framework, libraries>. Remote: `<git_remote_url>`

**Connections:**
- **Upstream:** ...
- **Downstream:** ...
- **Infrastructure:** <shared_db_brokers_services>
- **Part of:** ...
- **Decomposed from:** ...

**Key implementation notes:** <non_obvious_details_with_links_to_kb>
```

**2. `pattern`**

```markdown
---
id: <uuid>
title: <pattern_name> — <origin_project> proof-of-concept
type: pattern
tags:
- <project>
---
<problem_definition_and_mechanism>

**Proof-of-concept:** [<project>](kb://<uuid>#hub), `<file_or_module_path>`
**Pattern benefits:** (1) ...; (2) ...; (3) ...
**Adoption:** <where_used_or_planned>
```

**3. `integration`**

```markdown
---
id: <uuid>
title: <external_api_or_library> client — <project>
type: integration
tags:
- <project>
resource: <module_path>
---
**What it integrates with:** <system_name_and_version>
**Key details:** <auth, retries, payload_format, limits>
**Gotchas:** <external_quirks_found>

[Back to hub](kb://<uuid>#hub)
```

**4. `feature`**

```markdown
---
id: <uuid>
title: <feature_name> — <key_architecture_detail>
type: feature
tags:
- <project>
---
<feature_behavior_and_data_relation>

**Decision: <variant>** — <one_line_choice_summary>

**Implementation:**
- <models>
- <services>
- <routes>

**Why <variant>:** <trade_offs_and_rationale>

[Back to hub](kb://<uuid>#hub)
```

**5. `decision`**

```markdown
---
id: <uuid>
title: <decision> — <chosen_option>
type: decision
tags:
- <project>
---
**Context:** <the_problem_or_fork_in_the_road>
**Options considered:** <option_A, option_B>
**Chosen:** <option> — <one_line_reason>
**Trade-offs / follow-up:** <what_was_deferred_or_sacrificed>

[Back to hub](kb://<uuid>#hub)
```

**6. `diagnostic`**

```markdown
---
id: <uuid>
title: <bug_symptom>
type: diagnostic
tags:
- <project>
---
**Symptom:** ...
**Root cause:** ...
**Fix:** <changes_with_files_or_commits>
**Prevention:** <how_to_prevent_recurrence>

[Back to hub](kb://<uuid>#hub)
```

**7. `procedure`**

```markdown
---
id: <uuid>
title: <procedure_name>
type: procedure
tags:
- <project>
---
**When to use:** ...
**Steps:**
1. ...
2. ...
**Gotchas:** <easy_to_miss_details>

[Back to hub](kb://<uuid>#hub)
```

**8. `preference`**

```markdown
---
id: <uuid>
title: <preference_area>
type: preference
tags:
- <scope>
---
**Preference:** <user_workflow_or_coding_choice>
**Why / context:** ...
**Scope:** <always | project_X | language_Y>
```

`<scope>` replaces the project tag when the preference isn't project-specific (e.g. `python`, `git`, `testing`).

**9. `snippet`**

```markdown
---
id: <uuid>
title: <what_the_snippet_does>
type: snippet
tags:
- <project_or_lang>
---
**Problem it solves:** ...

\`\`\`<lang>
<code>
\`\`\`

**Usage note:** <when_and_how_to_apply>

[Back to hub](kb://<uuid>#hub)
```

## Memory work requires no confirmation

Searching, recalling, or writing to Engram are local, reversible actions (entries can be corrected via `forget`/`remember` again) — never pause to ask the user for permission before calling an Engram tool. This is background housekeeping, not a user-facing action.

## Search requirement before remember

The server requires a search or recall to be executed in the current session before remember can be used — if it's denied unexpectedly, run a search first, then retry `remember`.
