#!/usr/bin/env python3
"""PostToolUse hook: accumulate per-session token/cost usage into shared state.

No tool-name matcher: Claude Code only attaches `total_cost_usd` and
`usage.input_tokens`/`usage.output_tokens` to the PostToolUse payload for
calls that report their own cost (currently Agent-tool subagent calls), so
this fires on every PostToolUse event but is a no-op whenever those fields
are absent. Accumulates into the same per-session state file the other hooks
in this plugin share (_session_state.py: tokens_in, tokens_out, cost_usd),
so engram_statusline.py can surface running cost alongside the existing
counters. Local-only in v1 (kb://b47cdbc9) — no join against Engram's own
query_log, no network call.
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

    total_cost_usd = payload.get("total_cost_usd")
    usage = payload.get("usage") or {}
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")

    if total_cost_usd is None and tokens_in is None and tokens_out is None:
        return

    session_id = payload.get("session_id", "unknown")
    session_state = state.load(session_id)
    session_state["cost_usd"] += total_cost_usd or 0.0
    session_state["tokens_in"] += tokens_in or 0
    session_state["tokens_out"] += tokens_out or 0
    state.save(session_id, session_state)
    log(
        "PostToolUse",
        f"session={session_id} cost_usd={session_state['cost_usd']:.4f} "
        f"tokens_in={session_state['tokens_in']} tokens_out={session_state['tokens_out']}",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fail open — a broken hook must never block legitimate tool use.
        log("PostToolUse", f"unhandled exception: {exc!r}")
