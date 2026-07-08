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
New Claude Code session started. Searching Engram (the persistent-memory MCP server) is mandatory at the start of every single user request, no exceptions — code changes, planning, questions, discussion, review, anything. 
Search (mcp__engram__search / mcp__engram__recall) before your first substantive action, regardless of how small, familiar, or 'obviously just a chat' the request looks — relevance is not yours to pre-judge. 
If this project directory has no Engram hub entry yet (search/list/tags turn up nothing for it), that's a gap to fix via the engram-project-onboarder subagent.
"""


def main() -> None:
    if os.environ.get("ENGRAM_HOOKS_DISABLE") == "1":
        log("SessionStart", "disabled via ENGRAM_HOOKS_DISABLE, skipping")
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
