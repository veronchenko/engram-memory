#!/usr/bin/env python3
"""SessionStart hook: remind to search Engram before starting work.

Fires once per session, unconditionally — SessionStart isn't in Claude
Code's prompt-hook-supported event list, so this is a plain command hook
that always emits the reminder via hookSpecificOutput.additionalContext.
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

PROMPT = """
New Claude Code session started. Search Engram (mcp__engram__search / mcp__engram__recall) before the first substantive action on every user request, and again when the topic shifts.
If this project directory has no Engram hub entry yet (search/list/tags turn up nothing for it), fix that via the engram-project-onboarder subagent.
"""


def main() -> None:
    if os.environ.get("ENGRAM_SESSION_START_DISABLE") == "1":
        log("SessionStart", "disabled via ENGRAM_SESSION_START_DISABLE, skipping")
        return
    log("SessionStart", "emitting Engram search reminder")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": PROMPT,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block session startup.
        log("SessionStart", f"unhandled exception: {exc!r}")
