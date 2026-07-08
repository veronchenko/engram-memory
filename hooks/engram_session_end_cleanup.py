#!/usr/bin/env python3
"""SessionEnd hook: delete this session's Stop-counter state file.

engram_stop_prompt.py persists a per-session Stop count to a file under the
system temp dir (see its docstring). SessionEnd hooks can't block session
termination or talk to Claude, only run cleanup, so this just removes that
one file. Fails open silently — a missing file, permission error, or any
other issue here must never surface to the user or block session exit.
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


def _state_path(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"engram_stop_count_{session_id}.txt")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id")
    if not session_id:
        log("SessionEnd", "no session_id in payload, nothing to clean up")
        return

    path = _state_path(session_id)
    try:
        os.remove(path)
        log("SessionEnd", f"removed counter file for session={session_id}")
    except FileNotFoundError:
        log("SessionEnd", f"no counter file to remove for session={session_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — cleanup must never block or error out session exit.
        log("SessionEnd", f"unhandled exception: {exc!r}")
