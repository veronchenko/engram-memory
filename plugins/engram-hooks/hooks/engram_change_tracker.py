#!/usr/bin/env python3
"""PostToolUse hook: increment the per-session change-counter after a real edit.

Fires on Edit, Write, NotebookEdit unconditionally, and on Bash only when the
command looks like `git commit`. engram_stop_prompt.py reads this counter and
blocks Stop once it crosses ENGRAM_CHANGE_THRESHOLD file edits since the last
mcp__engram__remember call — engram_remember_gate.py resets it back to zero
on every successful remember, and engram_stop_prompt.py resets it whenever
either Stop trigger fires. Research/web tools are deliberately not counted.
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _hooklog import log
except Exception:
    def log(hook_name: str, message: str) -> None:
        pass

BASH_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _change_count_path(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"engram_change_count_{session_id}.txt")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")

    if tool_name == "Bash":
        command = payload.get("tool_input", {}).get("command", "")
        if not BASH_COMMIT_RE.search(command):
            return
    elif tool_name not in EDIT_TOOLS:
        return

    path = _change_count_path(session_id)
    try:
        with open(path, encoding="utf-8") as f:
            count = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(count))
    log("PostToolUse", f"session={session_id} change_count={count} (tool={tool_name})")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block legitimate tool use.
        log("PostToolUse", f"unhandled exception: {exc!r}")
