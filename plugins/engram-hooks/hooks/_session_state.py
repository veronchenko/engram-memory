"""Shared per-session state store for the Engram hooks in this folder.

Replaces the earlier one-counter-per-file scheme (engram_change_count_*.txt,
engram_stop_count_*.txt, engram_searched_*.txt) with a single JSON file per
session under the system temp dir, so a hook that needs more than one field
(the statusline script wants remembers_count and last_remember_ts alongside
the existing counters, engram_cost_tracker.py wants tokens_in/tokens_out/
cost_usd) doesn't need yet another file.

Each hook invocation is a fresh process with no memory of prior calls, so
load()/save() always round-trip through disk. Callers own their own
read-modify-write; this module does no locking, matching the concurrency
profile of the file-per-counter scheme it replaces (Claude Code runs a
session's hooks sequentially, never in parallel, so this has never needed
one).
"""

import json
import os
import tempfile

DEFAULTS = {
    "change_count": 0,
    "stop_count": 0,
    "searched": False,
    "remembers_count": 0,
    "last_remember_ts": None,
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_usd": 0.0,
}


def _path(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"engram_session_{session_id}.json")


def load(session_id: str) -> dict:
    try:
        with open(_path(session_id), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return {**DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return dict(DEFAULTS)


def save(session_id: str, state: dict) -> None:
    with open(_path(session_id), "w", encoding="utf-8") as f:
        json.dump(state, f)


def remove(session_id: str) -> None:
    try:
        os.remove(_path(session_id))
    except FileNotFoundError:
        pass
