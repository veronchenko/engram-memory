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


## Entry templates (strict format)

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
