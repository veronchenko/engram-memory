#!/usr/bin/env python3
"""SessionEnd hook: delete this session's hook state file.

engram_stop_prompt.py, engram_change_tracker.py, and engram_remember_gate.py
all persist per-session counters/flags via the shared _session_state.py
(one JSON file per session under the system temp dir — see its docstring).
SessionEnd hooks can't block session termination or talk to Claude, only
run cleanup, so this just removes that file. Fails open silently — a
missing file, permission error, or any other issue here must never surface
to the user or block session exit.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _hooklog import log
except Exception:
    def log(hook_name: str, message: str) -> None:
        pass
import _session_state as state


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id")
    if not session_id:
        log("SessionEnd", "no session_id in payload, nothing to clean up")
        return

    state.remove(session_id)
    log("SessionEnd", f"removed session state for {session_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — cleanup must never block or error out session exit.
        log("SessionEnd", f"unhandled exception: {exc!r}")
