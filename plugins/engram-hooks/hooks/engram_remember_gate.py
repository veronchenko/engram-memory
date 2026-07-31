#!/usr/bin/env python3
"""PreToolUse hook: refuse mcp__engram__remember until search/recall ran.

Fires on mcp__engram__search, mcp__engram__recall, and mcp__engram__remember
(matcher covers all three so one script handles both sides of the gate).
On search/recall it marks the session state "searched". On remember it
checks that flag: missing -> deny the tool call and tell the model to
search first. This is a session-level gate (any prior search/recall in the
session satisfies it) — it does not check topical relevance between the
search and the remember.

On a successful remember it also resets engram_stop_prompt.py's counters
(a remember satisfies whichever trigger was accumulating) and bumps
remembers_count / last_remember_ts, which the statusline script reads —
all via the shared per-session state in _session_state.py.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _hooklog import log
except Exception:
    def log(hook_name: str, message: str) -> None:
        pass
import _session_state as state

SEARCH_TOOLS = {"mcp__engram__search", "mcp__engram__recall"}
REMEMBER_TOOL = "mcp__engram__remember"

DENY_REASON = (
    "No mcp__engram__search or mcp__engram__recall call has happened yet this "
    "session. Search or recall first, then call remember — writing a memory "
    "without checking what's already known risks duplicate or contradicting "
    "entries. If you already searched, this is a fresh session state issue; "
    "search again to clear it."
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")

    if tool_name in SEARCH_TOOLS:
        session_state = state.load(session_id)
        session_state["searched"] = True
        state.save(session_id, session_state)
        log("PreToolUse", f"session={session_id} marked searched via {tool_name}")
        return

    if tool_name == REMEMBER_TOOL:
        session_state = state.load(session_id)
        if session_state["searched"]:
            session_state["change_count"] = 0
            session_state["stop_count"] = 0
            session_state["remembers_count"] += 1
            session_state["last_remember_ts"] = time.time()
            state.save(session_id, session_state)
            log("PreToolUse", f"session={session_id} remember allowed, stop counters reset, remembers_count={session_state['remembers_count']}")
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
