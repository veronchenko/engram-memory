#!/bin/sh
# By default starts only the MCP server (backend). Set ENGRAM_ENABLE_DASHBOARD
# to a truthy value (1/true/yes) to also start the dashboard (API + frontend)
# as a second process. Backgrounding a command with `&` in this shell closes
# its stdin (redirects to /dev/null) unless the real stdin is explicitly
# preserved first — otherwise a stdio MCP client's input never reaches the
# backgrounded backend, which then reads immediate EOF and exits right away
# as if the client had disconnected. `exec 3<&0` saves the real stdin on fd 3
# before backgrounding so the backend can read from it via `<&3`. The
# container exits as soon as either process exits (notably the MCP backend
# on real stdin EOF) so `docker run --rm` still cleans up correctly for
# stdio usage even with the dashboard enabled.
set -e

case "$ENGRAM_ENABLE_DASHBOARD" in
    1|true|TRUE|yes|YES)
        exec 3<&0
        python server.py <&3 &
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
