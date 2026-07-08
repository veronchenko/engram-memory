"""Shared debug-logging helper for the Engram hooks in this folder.

Writes timestamped lines to hooks/logs/debug.log, next to the hook scripts
themselves, so hook behavior can be inspected without --debug transcripts.
Disable by setting ENGRAM_HOOKS_DEBUG=0. Logging failures are swallowed —
a broken log write must never block or break a hook.

Retention: the log is capped at MAX_LOG_BYTES. Once it hits the cap it is
cleared (not rotated/zipped) before the next write — this is a debug log,
not an audit trail, so history isn't worth preserving.
"""

import datetime
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "debug.log")
MAX_LOG_BYTES = int(os.environ.get("ENGRAM_HOOKS_LOG_MAX_BYTES", 5 * 1024 * 1024))


def log(hook_name: str, message: str) -> None:
    if os.environ.get("ENGRAM_HOOKS_DEBUG") == "0":
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_BYTES:
            open(LOG_FILE, "w", encoding="utf-8").close()
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {hook_name}: {message}\n")
    except OSError:
        pass
