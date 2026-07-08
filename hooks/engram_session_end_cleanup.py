#!/usr/bin/env python3
"""SessionEnd hook: delete this session's hook state files.

engram_stop_prompt.py persists a per-session Stop count, and
engram_remember_gate.py persists a per-session search/recall marker, each to
a file under the system temp dir (see their docstrings). SessionEnd hooks
can't block session termination or talk to Claude, only run cleanup, so this
just removes those files. Fails open silently — a missing file, permission
error, or any other issue here must never surface to the user or block
session exit.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _hooklog import log
except Exception:
    def log(hook_name: str, message: str) -> None:
        pass


def _state_paths(session_id: str) -> list[str]:
    tmp = tempfile.gettempdir()
    return [
        os.path.join(tmp, f"engram_stop_count_{session_id}.txt"),
        os.path.join(tmp, f"engram_searched_{session_id}.txt"),
    ]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id")
    if not session_id:
        log("SessionEnd", "no session_id in payload, nothing to clean up")
        return

    for path in _state_paths(session_id):
        try:
            os.remove(path)
            log("SessionEnd", f"removed state file {path}")
        except FileNotFoundError:
            log("SessionEnd", f"no state file to remove at {path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — cleanup must never block or error out session exit.
        log("SessionEnd", f"unhandled exception: {exc!r}")
