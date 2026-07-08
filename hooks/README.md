# Engram usage hooks

Claude Code hooks that mechanically nudge an agent to use the Engram MCP
memory server, instead of relying on it remembering an ENGRAM_MEMORY.md
instruction every turn. Prompts are kept in sync with the rules in
`~/.claude/ENGRAM_MEMORY.md` — update both together when that file changes.

Lives here (`~/.claude/hooks/`, not in the engram_memory repo) since it's a
personal, global config edited directly, not a distributable repo artifact.

## Reference copies in this folder

- **`ENGRAM_MEMORY.md`** — verbatim copy of `~/.claude/ENGRAM_MEMORY.md`, the
  global instructions file that defines the "when to search"/"when to write"
  rules the three hooks below mechanically enforce. Kept here so the hook
  logic and the policy it's enforcing sit side by side and can be diffed
  against each other. **The live file is `~/.claude/ENGRAM_MEMORY.md`** —
  this copy is read-only reference and must be re-copied by hand whenever
  the original changes.
- **`engram-project-onboarder.md`** — verbatim copy of the
  `engram-project-onboarder` subagent definition (originally at
  `~/.claude/agents/engram-project-onboarder.md`). Referenced by
  `ENGRAM_MEMORY.md`'s "Project hygiene" section as the agent that creates a
  project's hub entry. Same caveat: the live copy Claude Code actually loads
  is under `~/.claude/agents/`, not here.

Three hooks, defined in `hooks.json`:

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
  cleanup.

All three hooks are plain Python command scripts (`type: "command"`), not
`type: "prompt"` hooks — `SessionStart` and `SessionEnd` aren't in Claude
Code's prompt-hook-supported event list, and `Stop` needs persisted state
(the per-session stop counter) that a stateless prompt hook can't keep. The
counter is stored per `session_id` in a small file under the system temp
dir (`engram_stop_count_<session_id>.txt`) since each hook invocation is a
fresh process with no memory of prior calls. Override the interval with the
`ENGRAM_STOP_INTERVAL` environment variable.

## Wiring

Wired into `~/.claude/settings.json` (the source of truth — `hooks.json` in
this folder is a reference copy, not auto-loaded). The three handlers were
merged into `settings.json`'s existing `hooks` key, alongside the pre-existing
`PreToolUse` (`rtk hook claude`) and `Stop` (`engram rebuild` MCP tool) entries
— `Stop` now has two handlers that both run on every Stop event. Since this
isn't a plugin, `${CLAUDE_PLUGIN_ROOT}` isn't available there; the commands
use the literal path `C:/Users/<username>/.claude/hooks/<script>.py` instead.

If any script here changes location or is renamed, update the matching
`command` path in `settings.json` too — it isn't derived from this file.

## Setup (fresh machine / new global config)

1. Copy this whole `hooks/` folder to `~/.claude/hooks/` (i.e.
   `C:/Users/<user>/.claude/hooks/` on Windows). The scripts assume they live
   there — `_hooklog.py` writes to `hooks/logs/debug.log` next to itself.
2. Make sure the Engram MCP server itself is registered and has a knowledge
   base directory to write to — these hooks only nudge an agent to *call*
   Engram's tools, they don't stand up the server. Create the data directory
   (e.g. `~/.claude/engram/knowledge`) if it doesn't exist yet — the server
   creates the `entries/` and `index/` subfolders under it on first run, but
   the parent directory needs to exist and be the one mounted into the
   `engram` MCP server (`claude mcp add ... -v <path>:/knowledge ...`, see
   the main repo README's Quick Start). Without this, `search`/`remember`
   calls the hooks prompt for will fail against an unconfigured server.
3. Open `~/.claude/settings.json` and merge the three handlers below into its
   `hooks` key (create the key if it doesn't exist yet). Use the **literal
   absolute path** to each script — `${CLAUDE_PLUGIN_ROOT}` only resolves
   inside a plugin, not in global `settings.json`:

   ```json
   {
     "hooks": {
       "SessionStart": [
         { "hooks": [
           { "type": "command", "command": "python \"C:/Users/<user>/.claude/hooks/engram_session_start.py\"", "timeout": 10 }
         ] }
       ],
       "Stop": [
         { "hooks": [
           { "type": "command", "command": "python \"C:/Users/<user>/.claude/hooks/engram_stop_prompt.py\"", "timeout": 20 }
         ] }
       ],
       "SessionEnd": [
         { "hooks": [
           { "type": "command", "command": "python \"C:/Users/<user>/.claude/hooks/engram_session_end_cleanup.py\"", "timeout": 5 }
         ] }
       ]
     }
   }
   ```

   If `SessionStart`/`Stop`/`SessionEnd` already have handlers (e.g. an
   existing `Stop` → `engram rebuild` MCP tool entry), **append** to that
   event's `hooks` array — don't replace it. Multiple handlers on the same
   event all run.
4. Make sure `~/.claude/ENGRAM_MEMORY.md` exists and is `@`-imported from
   `~/.claude/CLAUDE.md` — the hook prompts assume those rules are the ones
   in force and are kept in sync with them.
5. Verify `python` resolves on PATH for whatever shell Claude Code invokes
   hooks in (the commands call `python`, not a specific interpreter path).
6. Restart/start a new Claude Code session — `SessionStart` fires once per
   new session, so an already-running session won't pick up the change.
7. Sanity check: start a session and confirm `hooks/logs/debug.log` gets a
   `session_start` line; end the session and confirm the matching
   `engram_stop_count_<session_id>.txt` temp file is gone afterward.

No entry in `settings.json` currently references `hooks.json` in this
folder — it's kept only as a readable reference of what the merged config
should contain, so `hooks.json` and the live `settings.json` block must be
updated together by hand if the hook definitions change.
