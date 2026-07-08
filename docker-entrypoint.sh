#!/bin/sh
# By default starts only the MCP server (backend). Set ENGRAM_ENABLE_DASHBOARD
# to a truthy value (1/true/yes) to also start the dashboard (API + frontend)
# as a second process; the container's lifecycle then follows the dashboard
# process so signals (SIGTERM on `docker stop`) propagate correctly.
set -e

case "$ENGRAM_ENABLE_DASHBOARD" in
    1|true|TRUE|yes|YES)
        python server.py &
        MCP_PID=$!
        trap 'kill -TERM "$MCP_PID" 2>/dev/null' TERM INT

        python -m dashboard &
        DASHBOARD_PID=$!

        wait "$DASHBOARD_PID"
        ;;
    *)
        exec python server.py
        ;;
esac
