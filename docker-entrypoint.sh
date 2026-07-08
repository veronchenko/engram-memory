#!/bin/sh
# By default starts only the MCP server (backend). Set ENGRAM_ENABLE_DASHBOARD
# to a truthy value (1/true/yes) to also start the dashboard (API + frontend)
# as a second process. The container exits as soon as either process exits
# (notably the MCP backend on stdin EOF when a stdio client disconnects) so
# `docker run --rm` still cleans up correctly for stdio usage even with the
# dashboard enabled.
set -e

case "$ENGRAM_ENABLE_DASHBOARD" in
    1|true|TRUE|yes|YES)
        python server.py &
        MCP_PID=$!

        python -m dashboard &
        DASHBOARD_PID=$!

        trap 'kill -TERM "$MCP_PID" "$DASHBOARD_PID" 2>/dev/null || true' TERM INT

        while kill -0 "$MCP_PID" 2>/dev/null && kill -0 "$DASHBOARD_PID" 2>/dev/null; do
            sleep 1
        done

        kill -TERM "$MCP_PID" "$DASHBOARD_PID" 2>/dev/null || true
        wait
        ;;
    *)
        exec python server.py
        ;;
esac
