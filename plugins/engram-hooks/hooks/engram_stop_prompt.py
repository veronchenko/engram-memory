#!/usr/bin/env python3
"""Stop hook: remind to write non-trivial changes to Engram before finishing.

Command hook (not a "prompt" hook) — it has no way to judge the transcript
itself, so it unconditionally blocks with the reminder as the reason every
STOP_INTERVAL-th Stop event in a session, and relies on stop_hook_active (set
by Claude Code on the input JSON) to avoid re-blocking in an infinite loop.

Per-session Stop counts are persisted to a small state file in the system
temp dir, keyed by session_id, since each invocation of this script is a
fresh process with no memory of prior calls.
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

STOP_INTERVAL = int(os.environ.get("ENGRAM_STOP_INTERVAL", "5"))

PROMPT = """
Review the recent turns in this session's transcript (since the last time this reminder fired, or since the session started if it hasn't fired yet) against the rules for when to write to the Engram persistent-memory MCP server.
Across those turns, did the session:
(a) build or change something concrete (implemented/integrated/added/fixed) — via Edit, Write, NotebookEdit, or a `git commit`;
(b) make a choice among alternatives (library, pattern, schema, endpoint shape); (c) surface a surprising or non-obvious fact about a project (a constraint, a gotcha, a cross-project dependency);
(d) resolve a diagnostic (root cause + fix); (e) execute a non-trivial procedure; (f) capture a dev/tooling/project-structure preference the user stated or corrected;
or (g) produce a reusable snippet/pattern that took real effort?
If any of these happened AND mcp__engram__remember was NOT called for it, block with reason: 'Did the last few turns produce something that needs to survive past this conversation?
If so, call mcp__engram__remember before finishing. If you already judged there is nothing worth remembering, ignore this and proceed.'
If mcp__engram__remember already covered it, or this was pure read/Q&A with no new fact, or the assistant's immediately preceding message already addressed this exact reminder, approve with no message.
"""

def _state_path(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"engram_stop_count_{session_id}.txt")


def _next_count(session_id: str) -> int:
    path = _state_path(session_id)
    try:
        with open(path, encoding="utf-8") as f:
            count = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(count))
    return count


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if payload.get("stop_hook_active"):
        log("Stop", "stop_hook_active is true, skipping to avoid re-block loop")
        return

    session_id = payload.get("session_id", "unknown")
    count = _next_count(session_id)

    if count % STOP_INTERVAL != 0:
        log("Stop", f"session={session_id} count={count}/{STOP_INTERVAL}, letting stop proceed")
        return

    log("Stop", f"session={session_id} count={count}/{STOP_INTERVAL}, blocking with reminder")
    print(json.dumps({
        "decision": "block",
        "reason": PROMPT,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block the session from stopping.
        log("Stop", f"unhandled exception: {exc!r}")
