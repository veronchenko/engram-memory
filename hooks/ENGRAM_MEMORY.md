# Engram — Persistent Memory

Engram is an MCP server (`remember`, `recall`, `search`, `list`, `tags`, `forget`, `rebuild`) backed by a local knowledge base at `~/.claude/engram/knowledge`. It survives across projects and sessions. Using it is mandatory, not optional.

Engram stores ZERO discoverable information (except for project Hub entries — see "Project hygiene" below). If it can be derived from code, git history, config files, or existing documentation, it does not belong in Engram. Engram is for things that are lost when a conversation ends:

- **Dev preferences** — coding/tooling/workflow choices the user has stated or corrected me on, that aren't already in a CLAUDE.md.
- **Projects** — what each project is, where it lives, its stack, and any non-obvious context about it.
- **Previously implemented services/projects** — what was built, for which service/project, what it does, its stack and where it lives, so past work can be found and referenced instead of re-discovered or re-built from scratch.
- **Architecture decisions** — chosen approach, alternatives considered, and the reasoning, scoped to the project/component it applies to.
- **Project structure preferences** — how the user likes a project laid out (layering, module boundaries, folder conventions) when it isn't already codified in that project's CLAUDE.md.
- **Code snippets** — small reusable examples (a pattern, a config block, a one-liner) that solved a recurring problem and are worth pulling up again instead of re-deriving.
- **Diagnostics** — root causes of bugs/incidents and their fixes, once resolved.
- **Procedures** — steps for things learned the hard way (deploys, migrations, one-off setups).

## When to search Engram

- **At the start of every single user request, no exceptions** — code changes, planning, questions, discussion, review, anything. Search before the first substantive action, regardless of how small, familiar, or "obviously just a chat" the request looks. Not just "when relevant" — relevance is not mine to pre-judge, since the point of Engram is surfacing context I don't know I'm missing. This is not conditional on request type — the examples below are illustrations, not a whitelist of when the rule applies.
- Before answering a question about infrastructure, architecture, or a project's status.
- Before proposing a solution or a plan — check whether a past decision on this already exists.
- Before building something new — check whether a similar service/feature was already implemented elsewhere, or whether another project already has something I could reuse or must stay consistent with.

## Project hygiene — every project is a hub entry in Engram

Whenever I'm working in a project directory and Engram has no entry for it (search/list/tags turn up nothing), that's a gap to fix, not a fact to shrug off: **create one before or during the session**. Delegate this to the `engram-project-onboarder` subagent (via the Agent tool) — it investigates the project (CLAUDE.md/README, manifest, git remote, layout) and writes the hub plus linked entries per the schema below, so this doesn't have to be done by hand each time. This entry acts as a **hub**: a future session in a *different* project has no access to this project's CLAUDE.md/README, so the hub always states what the project does in full, even if that's also written elsewhere — that's the one exception to "zero discoverable information," because discoverable-from-within-this-repo isn't the same as discoverable-from-another-project's-session.

The hub entry always includes:

- **What it does** — one or two sentences, purpose/domain, with the project folder path set in the `resource` field (remote URL noted in the body). Always present, regardless of whether a CLAUDE.md/README already says it.
- **Connections to other projects** — what it calls, is called by, shares infra/DB with, or was split off from.

Beyond the hub, don't cram everything into one entry — write **separate linked entries** for each substantial thing tied to the project, and link them to the hub (and to each other) via `kb://uuid#type` references so the graph is traversable both ways (hub → details, detail → hub):

- **Implementations/services/integrations** — one entry per notable piece: what was built, what it does, its stack, specific enough that a session in another project can find and reuse it instead of rebuilding from scratch.
- **Features** — notable features and how they work, when non-obvious.
- **Ideas / future work** — things discussed or planned but not yet built.

Update the hub and its linked entries (don't recreate) as the project's shape changes materially — new service added, stack migration, new dependency on/from another project.

## Entry format standards

Every entry is one file with YAML-like frontmatter (`id`, `title`, `tags`, `type`, optional `resource`) plus a body. Entries link to each other via `kb://<uuid>#<type>` references — the graph must be traversable both ways (hub → detail, detail → hub).

`type`: **required** on every `remember` call (table below) — a dedicated frontmatter field, passed as its own `entry_type` argument, separate from `tags`. `remember` rejects the call with `{"error": "entry_type is required"}` if it's missing or empty. Legacy entries written before this field existed still read fine; the requirement only applies to new writes.

`tags`: one or more project tags (`snake_case`, matching the project's directory name), followed by optional topical tags (`mcp`, `auth`, `rag`, ...). Tags no longer double as the type marker — don't put the type in `tags` too.

`resource`: a filesystem path — the project's folder for `hub` entries, the specific file (or module path) for everything else (`integration`, `feature`, `snippet`, ...). This replaces a separate "Where it lives" body line. Omit when there's no single file/folder the entry maps to.

Atomicity: `remember` enforces one decision per article via non-blocking warnings on the response — Markdown headers in `content`, more than 3 paragraphs, and size past 512 B (soft) / 1 KB (hard) all trigger a warning, though the write still succeeds. Keep content to one sentence stating the decision plus an optional short justification; split multi-decision content into separate linked entries instead.

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

Don't create an entry just to use a type — if it doesn't fit "When to write to Engram" below, skip it.

**`hub` template:**

```markdown
---
{id: <uuid>, title: <project_name> — <short description>, type: hub, tags: [<project_name>], resource: <absolute path to project folder>}
---

<1-2 sentences: what it is, domain, port/env, part of which larger system.>

**What it does:** <functionality, key CRUD/endpoints/flows>

**Stack:** <language, framework, ORM, DI, key libraries>. Remote: `<git remote URL>`.

**Connections:**
- **Upstream:** <who calls this and how>
- **Downstream:** <what this calls>
- **Infrastructure:** <shared DB/brokers/third-party services>
- **Part of:** <parent system, sibling services>
- **Decomposed from / Split off from:** <if applicable>

**Key implementation notes:** <non-obvious details — identity patterns, soft/hard delete, specific output formats — with [links](kb://<uuid>#<type>) to related entries>
```

**`pattern` template:**

```markdown
---
{id: <uuid>, title: <pattern name> — <origin project> proof-of-concept, type: pattern, tags: [<project_name>, ...]}
---

<Pattern definition: problem it solves and general mechanism — 2-4 sentences.>

**Proof-of-concept:** [<project_name>](kb://<uuid>#hub), `<file/module path with the implementation>`.

**Pattern benefits:** (1) ...; (2) ...; (3) ...

**Adoption:** <which other projects/services already use or are migrating to it, where it's documented>
```

**`integration` template:**

```markdown
---
{id: <uuid>, title: <external system/API/library> client — <project_name>, type: integration, tags: [<project_name>], resource: <path/module in the project>}
---

**What it integrates with:** <external API/service/broker/library, version if relevant>

**Key details:** <auth method, retry/backoff, message/payload format, rate limits, anything a reimplementer would need>

**Gotchas:** <quirks of the external system found the hard way, if any>

[Back to hub](kb://<uuid>#hub)
```

**`feature` template:**

```markdown
---
{id: <uuid>, title: <feature name> — <key architectural detail>, type: feature, tags: [<project_name>]}
---

<What the feature is, how the data relates (model) — 2-3 sentences.>

**Decision: <variant/approach>** — <one-line summary of the choice>

<Why this approach was chosen this way, if not obvious.>

**Implementation:**
- <tables/models>
- <repositories/services>
- <routes/endpoints>
- <key behavior>

**Why <variant>:** <reason for the choice, trade-offs, what was left out>

[Back to hub](kb://<uuid>#hub)
```

**`decision` template** (for choices without a standalone artifact):

```markdown
---
{id: <uuid>, title: <what was decided> — <chosen option>, type: decision, tags: [<project_name>]}
---

**Context:** <the problem/fork in the road>

**Options considered:** <option A, option B, ...>

**Chosen:** <option> — <one-line reason>

**Trade-offs / follow-up:** <what was knowingly deferred or sacrificed>

[Back to hub](kb://<uuid>#hub)
```

**`diagnostic` template:**

```markdown
---
{id: <uuid>, title: <bug symptom>, type: diagnostic, tags: [<project_name>]}
---

**Symptom:** <how the bug/incident presented>

**Root cause:** <exact cause>

**Fix:** <what was changed, file/commit if useful>

**Prevention:** <if applicable — what prevents recurrence>

[Back to hub](kb://<uuid>#hub)
```

**`procedure` template:**

```markdown
---
{id: <uuid>, title: <procedure name>, type: procedure, tags: [<project_name>]}
---

**When to use:** <circumstances>

**Steps:**
1. ...
2. ...

**Gotchas:** <easy-to-miss details learned the hard way>

[Back to hub](kb://<uuid>#hub)
```

**`preference` template:**

```markdown
---
{id: <uuid>, title: <preference area>, type: preference, tags: [<scope>]}
---

**Preference:** <what the user prefers>

**Why / context:** <if the user explained a reason>

**Scope:** <always | only in project X | only for language Y>
```

`<scope>` replaces the project tag when the preference isn't project-specific (e.g. `python`, `git`, `testing`).

**`snippet` template:**

```markdown
---
{id: <uuid>, title: <what the snippet does>, type: snippet, tags: [<project_name or language/area>]}
---

**Problem it solves:** <one line>

\`\`\`<lang>
<code>
\`\`\`

**Usage note:** <when/how to apply, if not obvious>
```

## When to write to Engram

**After finishing any non-trivial request or task — especially one involving code changes — pause and ask: did anything here need to survive past this conversation?** This is a mandatory closing step, not a maybe. Don't wait for the trigger list below to match verbatim; actively look for the shape of "important point" even if it doesn't fit a named category:

- A concrete thing got built or changed: *"implemented X"*, *"integrated with service Y"*, *"added endpoint Z"* — capture what it does, its stack/approach, and where it lives, even if it's a small piece, so it's findable later instead of rediscovered.
- A choice was made among alternatives (library, pattern, schema, endpoint shape) — capture the choice and the one-line reason, not just the fact that a decision happened.
- Something surprising or non-obvious about a project/service was learned in the process (a constraint, a gotcha, a dependency on another project) — that's exactly the kind of thing code doesn't self-document.

If none of the above happened (pure read, pure Q&A with no new fact), skip — don't force an entry for the sake of it.

- After resolving a diagnostic: remember the root cause and the fix.
- After executing a non-trivial procedure: remember the steps.
- After making an architecture/tooling decision: remember the choice and why.
- After the user states or corrects a development or project-structure preference.
- After writing a snippet/pattern that took real effort to get right and will likely be reused.
- After finishing (or substantially progressing) a service/project: remember what it does, its stack, and where it lives.
- Tag entries with the project name so `search`/`list` can filter by it.

## Memory work requires no confirmation

Searching, recalling, or writing to Engram are local, reversible actions (entries can be corrected via `forget`/`remember` again) — never pause to ask the user for permission before calling an Engram tool. This is background housekeeping, not a user-facing action.
