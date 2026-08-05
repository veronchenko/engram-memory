#!/usr/bin/env python3
"""Claude Code statusLine script for the engram-hooks workflow.

Not wired via hooks.json: plugin settings.json only supports the `agent`
and `subagentStatusLine` keys, not the main `statusLine` (see plugins
reference), so a plugin cannot auto-register this. Point your own
~/.claude/settings.json at it manually:

    "statusLine": {
        "type": "command",
        "command": "python \"<path-to-this-file>\""
    }

Reads the statusline JSON payload Claude Code sends on stdin (model,
workspace, context_window, session_id) plus this session's Engram hook
state (_session_state.py, written by engram_change_tracker.py,
engram_stop_prompt.py, and engram_remember_gate.py) to show: model, git
branch, context-window usage (percent + token count), change-counter and
self-report progress toward their thresholds, and remembers written this
session. engram_cost_tracker.py also writes tokens_in/tokens_out/cost_usd
into the same state file, but this script deliberately doesn't surface
them — cost tracking stays local-only data, not a statusline metric.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _session_state as state

# Claude Code captures stdout rather than connecting to the terminal, but on
# Windows the interpreter still defaults stdout's encoding to the console's
# active codepage (e.g. cp1251), which can't encode the emoji segments below
# and raises UnicodeEncodeError. Force UTF-8 regardless of platform/codepage.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CHANGE_THRESHOLD = int(os.environ.get("ENGRAM_CHANGE_THRESHOLD", "15"))
STOP_INTERVAL = int(os.environ.get("ENGRAM_STOP_INTERVAL", "5"))


def _git_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        branch = result.stdout.strip()
        return branch or None
    except (OSError, subprocess.SubprocessError):
        return None


def _format_tokens(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    model = payload.get("model", {}).get("display_name", "?")
    cwd = payload.get("workspace", {}).get("current_dir") or payload.get("cwd", "")
    session_id = payload.get("session_id", "unknown")

    segments = [f"[{model}]"]

    branch = _git_branch(cwd) if cwd else None
    if branch:
        segments.append(f"\U0001f33f {branch}")

    ctx = payload.get("context_window") or {}
    used_pct = ctx.get("used_percentage")
    size = ctx.get("context_window_size")
    if used_pct is not None and size:
        used_tokens = (ctx.get("total_input_tokens") or 0) + (ctx.get("total_output_tokens") or 0)
        segments.append(f"{used_pct:.0f}% · {_format_tokens(used_tokens)}/{_format_tokens(size)} tok")

    session_state = state.load(session_id)
    segments.append(f"\U0001f9e0 {session_state['change_count']}/{CHANGE_THRESHOLD}")
    segments.append(f"\U0001f4dd {session_state['stop_count']}/{STOP_INTERVAL}")
    segments.append(f"\U0001f4be {session_state['remembers_count']} remembers")

    print(" | ".join(segments))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("")
