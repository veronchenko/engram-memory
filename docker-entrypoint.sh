#!/bin/sh
# Starts the MCP server in the background and the dashboard in the
# foreground, so the container's lifecycle follows the dashboard process
# and signals (SIGTERM on `docker stop`) propagate correctly.
set -e

python server.py &
MCP_PID=$!
trap 'kill -TERM "$MCP_PID" 2>/dev/null' TERM INT

python -m dashboard &
DASHBOARD_PID=$!

wait "$DASHBOARD_PID"
