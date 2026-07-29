<!-- Source of truth: plugins/engram-hooks/ in the engram_memory repo. The ~/.claude copy is installed from here — edit the plugin copy, then re-install. -->

| type | Use for | part_of |
|---|---|---|
| `hub` | One per project/service — what it is, where it lives, stack, connections | — (it *is* the anchor other entries point at) |
| `pattern` | Reusable architectural pattern that originated in one place, applicable elsewhere | optional; the origin project is a semantic `kb://` link in the body, not membership |
| `integration` | A client/consumer/adapter implemented for a specific external API, service, broker, or library — so it's found and reused instead of rebuilt | required — the project's hub uuid |
| `feature` | Non-trivial feature inside a project and how it's built | required — the project's hub uuid |
| `decision` | Choice among alternatives without a standalone artifact (not substantial enough for `feature`) | required — the project's hub uuid |
| `diagnostic` | Root cause + fix of a resolved bug/incident | required — the project's hub uuid |
| `procedure` | Steps for a non-obvious procedure (deploy, migration, one-off setup) | required — the project's hub uuid |
| `preference` | Dev/tooling/process preference not already covered by a CLAUDE.md | never (global scope) |
| `snippet` | Small reusable code/config snippet | optional — the project's hub if project-specific |
| `idea` | Researched finding / candidate direction not yet acted on | optional — the project's hub it was researched for |


## Entry templates

The server generates the entry file, its UUID, and all frontmatter — never write YAML yourself. Each template lists the `remember` arguments to pass (`title`, `entry_type`, plus `resource`/`part_of` where the table requires them) and the body to pass as `content`. Omit `tags` entirely when no topical tag applies.

**1. `hub`** — `title: <project> — <short_desc>`, `resource: <absolute_path_to_folder>`

```markdown
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

**2. `pattern`** — `title: <pattern_name> — <origin_project> proof-of-concept`

```markdown
<problem_definition_and_mechanism>

**Proof-of-concept:** [<project>](kb://<uuid>#hub), `<file_or_module_path>`
**Pattern benefits:** (1) ...; (2) ...; (3) ...
**Adoption:** <where_used_or_planned>
```

**3. `integration`** — `title: <external_api_or_library> client — <project>`, `resource: <module_path>`, `part_of: [<hub_uuid>]`

```markdown
**What it integrates with:** <system_name_and_version>
**Key details:** <auth, retries, payload_format, limits>
**Gotchas:** <external_quirks_found>
```

**4. `feature`** — `title: <feature_name> — <key_architecture_detail>`, `part_of: [<hub_uuid>]`

```markdown
<feature_behavior_and_data_relation>

**Decision: <variant>** — <one_line_choice_summary>

**Implementation:**
- <models>
- <services>
- <routes>

**Why <variant>:** <trade_offs_and_rationale>
```

**5. `decision`** — `title: <decision> — <chosen_option>`, `part_of: [<hub_uuid>]`

```markdown
**Context:** <the_problem_or_fork_in_the_road>
**Options considered:** <option_A, option_B>
**Chosen:** <option> — <one_line_reason>
**Trade-offs / follow-up:** <what_was_deferred_or_sacrificed>
```

**6. `diagnostic`** — `title: <bug_symptom>`, `part_of: [<hub_uuid>]`

```markdown
**Symptom:** ...
**Root cause:** ...
**Fix:** <changes_with_files_or_commits>
**Prevention:** <how_to_prevent_recurrence>
```

**7. `procedure`** — `title: <procedure_name>`, `part_of: [<hub_uuid>]`

```markdown
**When to use:** ...
**Steps:**
1. ...
2. ...
**Gotchas:** <easy_to_miss_details>
```

**8. `preference`** — `title: <preference_area>`, `tags: [<scope>]`

```markdown
**Preference:** <user_workflow_or_coding_choice>
**Why / context:** ...
**Scope:** <always | project_X | language_Y>
```

`<scope>` is a topical tag naming the preference's domain (e.g. `python`, `git`, `testing`).

**9. `snippet`** — `title: <what_the_snippet_does>`, `part_of: [<hub_uuid>]` when project-specific

```markdown
**Problem it solves:** ...

\`\`\`<lang>
<code>
\`\`\`

**Usage note:** <when_and_how_to_apply>
```

**10. `idea`** — `title: <project>: <finding_or_candidate>`, `part_of: [<hub_uuid>]` when researched for a specific project

```markdown
<what_was_researched_and_what_was_found>

**Candidate:** <what_could_be_done_with_it>
**Status:** <not_decided | superseded_by_a_decision>
```

An `idea` becomes a `decision` or `feature` entry once it is acted on — supersede it then, don't leave both as current facts.
