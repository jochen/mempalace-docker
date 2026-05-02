#!/bin/sh
set -e

# Start mcp-proxy on internal port 8081 (stdio → SSE bridge)
mcp-proxy --port 8081 -- python -m mempalace.mcp_server &

# Give mcp-proxy a moment to bind the port
sleep 1

# Start auth proxy on external port 8080 (foreground, PID 1 replacement)
exec python auth_proxy.py
