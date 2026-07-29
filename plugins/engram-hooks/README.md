# Engram usage hooks

A self-contained plugin, installable in both **Claude Code** and **Codex**,
that mechanically nudges an agent to use the Engram MCP memory server,
instead of relying on it remembering an ENGRAM_SPEC.md instruction every
turn. Prompts are kept in sync with the rules in `~/.claude/ENGRAM_SPEC.md`
— update both together when that file changes.

One plugin serves both agents: Codex's plugin loader discovers
`.claude-plugin/plugin.json` as a documented OOTB-compatibility fallback (it
checks `.codex-plugin/plugin.json`, then `.claude-plugin/plugin.json`, then
`.cursor-plugin/plugin.json`, first match wins) and sets both `PLUGIN_ROOT`
and `CLAUDE_PLUGIN_ROOT` when running hook commands, so the same manifest and
`hooks.json` work unmodified under either client — no `.codex-plugin/`
duplicate needed.

## Plugin layout

```
plugins/
├── marketplace.json                  <- Codex marketplace (repo-relative source)
└── engram-hooks/                     <- plugin root (this folder)
    ├── .claude-plugin/
    │   └── plugin.json               <- shared manifest (Claude Code + Codex)
    ├── hooks/
    │   ├── hooks.json                <- event → command wiring
    │   ├── engram_remember_gate.py
    │   ├── engram_session_start.py
    │   ├── engram_stop_prompt.py
    │   ├── engram_session_end_cleanup.py
    │   └── _hooklog.py               <- shared debug-logging helper
    ├── agents/
    │   └── engram-project-onboarder.md   <- Claude Code subagent only (see below)
    ├── ENGRAM_SPEC.md                <- reference copy, see below
    ├── ENGRAM_TEMPLATES.md           <- reference copy, see below
    └── README.md                     <- this file
```

The repo root also has `.claude-plugin/marketplace.json` (Claude Code
marketplace), which lists `plugins/engram-hooks/` as the `engram-hooks`
plugin under the `engram-memory` marketplace. `plugins/marketplace.json`
does the equivalent for Codex, using Codex's own marketplace schema
(`source: {"source": "local", "path": "./engram-hooks"}`, `policy`,
`category` — Codex requires these fields, unlike Claude's simpler
bare-string `source`). Both marketplaces point at the same plugin directory.

`agents/engram-project-onboarder.md` is Claude-Code-only: Codex's
`plugin.json` schema has no `agents` field (only `name`, `version`,
`description`, `keywords`, `skills`, `mcpServers`, `apps`, `hooks`,
`interface` are recognized) — Codex's nearest equivalent to a Claude
subagent is a `skills/*/SKILL.md`, which this plugin doesn't need since the
onboarder is invoked as a dedicated subagent, not a skill. No action needed;
Codex simply won't load it, same as any other unrecognized directory.

## Reference copies in this folder

- **`ENGRAM_SPEC.md`** — verbatim copy of `~/.claude/ENGRAM_SPEC.md`, the
  global instructions file that defines the "when to search"/"when to write"
  rules the four hooks below mechanically enforce. Kept here so the hook
  logic and the policy it's enforcing sit side by side for reading.
  **The live file is `~/.claude/ENGRAM_SPEC.md`** — this copy is read-only
  reference. It pulls in `@ENGRAM_TEMPLATES.md` for the entry-type table and
  the 9 strict templates.
- **`ENGRAM_TEMPLATES.md`** — verbatim copy of `~/.claude/ENGRAM_TEMPLATES.md`,
  the entry-type table and strict templates `ENGRAM_SPEC.md` imports. Split
  out from the spec because it's pure vocabulary/wording — not enforced by
  the server (`entry_type` is any non-empty string) — so it's the part meant
  to be edited/replaced per user, unlike `ENGRAM_SPEC.md`.

  **Sync rule for both files above:** on install or update, hard-copy
  (overwrite) whichever direction is stale — never hand-merge or diff the
  two sides. These are the single exception to the general "review before
  applying" habit: copy the file wholesale and move on.

`agents/engram-project-onboarder.md` needs no such reference-copy dance —
as a Claude Code plugin agent it loads directly from here, so there's only
one live copy.

## The four hooks

Defined in `hooks/hooks.json`:

- **`PreToolUse`** (`engram_remember_gate.py`, matcher
  `mcp__engram__search|mcp__engram__recall|mcp__engram__remember`) — enforces
  "search before remember" mechanically instead of relying on the model to
  self-police it. On `search`/`recall` it stamps a per-session marker file in
  the system temp dir (`engram_searched_<session_id>.txt`); on `remember` it
  checks that marker and denies the call (`permissionDecision: "deny"`) if
  it's missing, telling the model to search first. Session-level gate — any
  prior search/recall in the session satisfies it, regardless of topic.
- **`SessionStart`** (`engram_session_start.py`) — fires once per new
  session, unconditionally reminds to search Engram
  (`mcp__engram__search`/`recall`) before starting work, per the "When to
  search Engram" rule (mandatory, every request, not conditional on
  relevance).
- **`Stop`** (`engram_stop_prompt.py`) — fires on every Stop event, but only
  emits the reminder every `ENGRAM_STOP_INTERVAL`-th time in a session
  (default 3). On the other turns it exits silently and lets the session
  stop normally. When it does fire, the reminder asks the model to check
  the "When to write to Engram" trigger list (built/changed something,
  chose among alternatives, learned something non-obvious, resolved a
  diagnostic, ran a procedure, captured a preference, wrote a reusable
  snippet) and blocks once so the model can call `mcp__engram__remember`
  before finishing.
- **`SessionEnd`** (`engram_session_end_cleanup.py`) — fires when the
  session actually ends (not on every Stop) and deletes that session's
  `engram_stop_count_<session_id>.txt` state file, so the temp dir doesn't
  accumulate one stale counter file per session forever. No decision
  control on this event (can't block exit, can't message Claude) — purely
  cleanup. **Codex-only gap:** Codex's hook event set (`PreToolUse`,
  `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`,
  `SessionStart`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`)
  has no `SessionEnd` equivalent, so this entry is silently ignored there
  (unknown hook-event keys don't fail parsing) and the two temp state files
  simply accumulate one pair per Codex session instead of being swept — not
  worth a workaround, since they're tiny and the OS temp dir clears
  periodically anyway.

All four hooks are plain Python command scripts (`type: "command"`), not
`type: "prompt"` hooks — `SessionStart` and `SessionEnd` aren't in Claude
Code's prompt-hook-supported event list, and `Stop`/`PreToolUse` need
persisted state (the per-session stop counter, the per-session search
marker) that a stateless prompt hook can't keep. Both state files live
under the system temp dir (`engram_stop_count_<session_id>.txt`,
`engram_searched_<session_id>.txt`) since each hook invocation is a fresh
process with no memory of prior calls. Override the Stop interval with the
`ENGRAM_STOP_INTERVAL` environment variable.

`${CLAUDE_PLUGIN_ROOT}` in `hooks/hooks.json` resolves to this plugin's
installed/cached directory at runtime under either client (Codex sets it as
an alias of its own `PLUGIN_ROOT` specifically for OOTB compatibility with
existing Claude Code plugins), so the commands work no matter where the
plugin is cached after install — that's why the scripts live under a nested
`hooks/` (the plugin-root-relative directory both clients expect hook
scripts under), not flattened into the plugin root.

## Install

The fastest path is `scripts/install.{ps1,sh}` from the repo root — it
detects whichever of `claude`/`codex` is on PATH and does everything below
(MCP server, plugin, global instructions) for each one automatically. See
"Setup" further down for exactly what it does and what's still manual.

To do it by hand, or to understand what the script automates:

### Claude Code

From any directory, inside a Claude Code session:

```
/plugin marketplace add <path-or-url-to-this-repo>
/plugin install engram-hooks@engram-memory
/reload-plugins
```

- Local development / testing this repo directly: `/plugin marketplace add
  C:/Users/<user>/Desktop/projects/My/engram_memory` (or wherever you cloned
  it), or skip the marketplace entirely and run
  `claude --plugin-dir ./plugins/engram-hooks` to load it for one session
  without installing.
- Shared/remote install: point `/plugin marketplace add` at this repo's git
  URL or `owner/repo` GitHub shorthand instead of a local path — the
  `engram-hooks` entry in the repo's `.claude-plugin/marketplace.json`
  resolves the same way.
- `/plugin install` opens a scope picker (user/project/local); pick `user`
  to have the hooks active in every project.

This replaces hand-copying this folder into `~/.claude/hooks/` and merging
`hooks.json` into `~/.claude/settings.json` — Claude Code now manages the
plugin's cache location and wires `hooks/hooks.json` in automatically once
installed and enabled.

### Codex

From the repo root:

```
codex plugin marketplace add ./plugins
codex plugin add engram-hooks@engram-memory
```

(or `codex plugin marketplace add <repo>/plugins` from elsewhere, and
`owner/repo` / a git URL for a shared/remote install, same as Claude Code).
Codex's `hooks` feature (`CodexHooks`) is stable and enabled by default, so
no `config.toml` feature flag is needed — installing and enabling the plugin
is enough. The `engram-project-onboarder` agent doesn't carry over (see
above); everything else does.

## Setup (fresh machine / new global config)

`scripts/install.ps1` (Windows) / `scripts/install.sh` (macOS/Linux) run all
of this in one pass, best-effort (a failing step warns and the script keeps
going rather than aborting):

1. **Detect** which of `claude` / `codex` are on PATH — everything below
   only happens for the CLI(s) actually present.
2. **Knowledge base directory.** Shared across both clients at
   `~/.engram/knowledge` (client-agnostic — Engram itself isn't tied to one
   agent). If an older `~/.claude/engram/knowledge` exists from a previous
   install, the script moves it to the new location instead of creating an
   empty one.
3. **MCP server + plugin, via each CLI's own commands** — `claude mcp add`
   + `claude plugin marketplace add`/`install`, and/or `codex mcp add` +
   `codex plugin marketplace add`/`add`, pointing at the shared knowledge
   dir and this repo's marketplace(s).
4. **Global instructions**, same shape for both clients: creates an
   `engram/` folder under each present client's home dir
   (`~/.claude/engram/`, `~/.codex/engram/`) holding copies of
   `ENGRAM_SPEC.md` + `ENGRAM_TEMPLATES.md`, then wires it into that
   client's global instructions file — `@engram/ENGRAM_SPEC.md` import line
   in `~/.claude/CLAUDE.md` for Claude Code; for Codex, which has no
   `@`-import in `AGENTS.md`, the two files' resolved content (with the
   `@ENGRAM_TEMPLATES.md` reference expanded inline) is written into
   `~/.codex/AGENTS.md` between `<!-- BEGIN/END engram-memory -->` markers,
   replaced in place on re-run rather than duplicated.

One thing stays manual regardless — a plugin/script can't do this safely on
its own:

- **Disable Claude Code's own built-in auto memory** (`MEMORY.md` under
  `~/.claude/projects/<project>/memory/`) — a separate system from Engram;
  running both means two systems writing overlapping notes. Set in
  `settings.json` (any scope):

  ```json
  { "autoMemoryEnabled": false }
  ```

  or via environment variable, which takes precedence over both the
  setting and the `/memory` toggle: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

Only Claude Code and Codex are supported so far.

Sanity check after installing: start a session and confirm
`hooks/logs/debug.log` (next to the scripts, inside the installed plugin's
cache directory) gets a `session_start` line; call `remember` before ever
calling `search`/`recall` and confirm it's denied; call `search` and retry
`remember` and confirm it now succeeds; end the session and confirm both
`engram_stop_count_<session_id>.txt` and `engram_searched_<session_id>.txt`
temp files are gone afterward (Claude Code only — see the Codex gap above).
