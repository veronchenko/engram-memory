#!/usr/bin/env python3
"""PreToolUse hook: refuse mcp__engram__remember until search/recall ran.

Fires on mcp__engram__search, mcp__engram__recall, and mcp__engram__remember
(matcher covers all three so one script handles both sides of the gate).
On search/recall it stamps a per-session marker file in the system temp dir.
On remember it checks that marker: missing marker -> deny the tool call and
tell the model to search first. This is a session-level gate (any prior
search/recall in the session satisfies it) — it does not check topical
relevance between the search and the remember.

State file is separate from the Stop-counter one so the two hooks don't
clobber each other's state; SessionEnd cleanup removes both.
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

SEARCH_TOOLS = {"mcp__engram__search", "mcp__engram__recall"}
REMEMBER_TOOL = "mcp__engram__remember"

DENY_REASON = (
    "No mcp__engram__search or mcp__engram__recall call has happened yet this "
    "session. Search or recall first, then call remember — writing a memory "
    "without checking what's already known risks duplicate or contradicting "
    "entries. If you already searched, this is a fresh session state issue; "
    "search again to clear it."
)


def _marker_path(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"engram_searched_{session_id}.txt")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")
    marker = _marker_path(session_id)

    if tool_name in SEARCH_TOOLS:
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(tool_name)
            log("PreToolUse", f"session={session_id} marked searched via {tool_name}")
        except OSError as exc:
            log("PreToolUse", f"failed to write marker: {exc!r}")
        return

    if tool_name == REMEMBER_TOOL:
        if os.path.exists(marker):
            log("PreToolUse", f"session={session_id} remember allowed, marker present")
            return
        log("PreToolUse", f"session={session_id} remember denied, no prior search/recall")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        }))
        return


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block legitimate tool use.
        log("PreToolUse", f"unhandled exception: {exc!r}")
