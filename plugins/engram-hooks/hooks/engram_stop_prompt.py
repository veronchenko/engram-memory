#!/usr/bin/env python3
"""Stop hook: hybrid reminder to write non-trivial changes to Engram.

Two independent triggers, checked in order:

1. Change-counter (mechanical) — fires once ENGRAM_CHANGE_THRESHOLD
   file-editing tool calls (Edit/Write/NotebookEdit/git commit) have
   happened since the last mcp__engram__remember call. Counted by
   engram_change_tracker.py's PostToolUse hook. The block reason states the
   fact plainly (no self-judgment needed to know the count is accurate), but
   still lets the model decide remember isn't warranted — not every batch of
   edits produces something worth persisting.
2. Self-report (judgment-based) — fires every ENGRAM_STOP_INTERVAL-th Stop
   event, asking the model to review the transcript for anything the
   change-counter can't see (a decision made purely in conversation, with no
   tool call at all — mechanically undetectable by any hook).

Whichever trigger fires resets BOTH counters (see _reset_counters), so the
two mechanisms never double-block back-to-back for the same underlying work.
mcp__engram__remember calls also reset both counters, via
engram_remember_gate.py. stop_hook_active (set by Claude Code on the input
JSON) avoids re-blocking in an infinite loop.

Per-session counts are persisted via _session_state.py to a single JSON
file per session in the system temp dir, since each invocation of this
script is a fresh process with no memory of prior calls.
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

STOP_INTERVAL = int(os.environ.get("ENGRAM_STOP_INTERVAL", "5"))
CHANGE_THRESHOLD = int(os.environ.get("ENGRAM_CHANGE_THRESHOLD", "15"))

SELF_REPORT_PROMPT = """
Review the recent turns in this session's transcript (since the last time this reminder fired, or since the session started if it hasn't fired yet) against the "When to write to Engram" rule in your persistent-memory specification.
If it applies and mcp__engram__remember was NOT called for it, call mcp__engram__remember before finishing. If you already judged there is nothing worth remembering, ignore this and proceed.
If mcp__engram__remember already covered it, or this was pure read/Q&A with no new fact, or the assistant's immediately preceding message already addressed this exact reminder, say nothing about this reminder and continue.
"""


def _reset_counters(session_state: dict) -> None:
    session_state["change_count"] = 0
    session_state["stop_count"] = 0


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if payload.get("stop_hook_active"):
        log("Stop", "stop_hook_active is true, skipping to avoid re-block loop")
        return

    session_id = payload.get("session_id", "unknown")
    session_state = state.load(session_id)

    change_count = session_state["change_count"]
    if change_count >= CHANGE_THRESHOLD:
        log("Stop", f"session={session_id} change_count={change_count}/{CHANGE_THRESHOLD}, blocking (change-counter)")
        _reset_counters(session_state)
        state.save(session_id, session_state)
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"{change_count} file-editing tool calls (Edit/Write/NotebookEdit/git commit) "
                "happened since the last mcp__engram__remember call. If any of that is worth "
                "keeping in Engram, call mcp__engram__remember for it now. If you already judged "
                "there's nothing worth remembering, ignore this and proceed."
            ),
        }))
        return

    session_state["stop_count"] += 1
    count = session_state["stop_count"]
    if count % STOP_INTERVAL != 0:
        state.save(session_id, session_state)
        log("Stop", f"session={session_id} stop_count={count}/{STOP_INTERVAL}, letting stop proceed")
        return

    log("Stop", f"session={session_id} stop_count={count}/{STOP_INTERVAL}, blocking (self-report)")
    _reset_counters(session_state)
    state.save(session_id, session_state)
    print(json.dumps({
        "decision": "block",
        "reason": SELF_REPORT_PROMPT,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block the session from stopping.
        log("Stop", f"unhandled exception: {exc!r}")
