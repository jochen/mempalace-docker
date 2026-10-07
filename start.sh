#!/bin/bash
set -u

READY_TIMEOUT="${BACKEND_READY_TIMEOUT:-120}"
APP_DIR="${APP_DIR:-/app}"

# auth_proxy.py is the only public entry point and does all auth (claude.ai
# sends OAuth tokens, not MCP_AUTH_TOKEN), so the backend runs without one.
unset MEMPALACE_MCP_HTTP_TOKEN

# A serverinfo.json left behind by a crash or SIGKILL survives in the volume
# and can make `mempalace mine` forward to a dead hub (PIDs repeat in
# containers). The backend writes a fresh one on start.
rm -f "$HOME"/.mempalace/server/*/serverinfo.json

BACKEND_PID=""
PROXY_PID=""
stop_all() {
    [ -n "$BACKEND_PID" ] && kill -TERM "$BACKEND_PID" 2>/dev/null
    [ -n "$PROXY_PID" ] && kill -TERM "$PROXY_PID" 2>/dev/null
}
trap 'stop_all; wait; exit 143' TERM INT

# MemPalace's native Streamable HTTP transport, loopback only
python -m mempalace.mcp_server --transport http --host 127.0.0.1 --port 8081 &
BACKEND_PID=$!

# Wait until the backend answers a real tool call (not just /healthz)
start=$SECONDS
until python "$APP_DIR/backend_ready.py"; do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        wait "$BACKEND_PID"
        code=$?
        echo "start.sh: MemPalace backend exited during startup (code $code)" >&2
        exit "$code"
    fi
    if (( SECONDS - start >= READY_TIMEOUT )); then
        echo "start.sh: MemPalace backend not ready after ${READY_TIMEOUT}s" >&2
        stop_all
        wait
        exit 1
    fi
    sleep 1
done
echo "start.sh: MemPalace backend ready after $((SECONDS - start))s"

# Public auth proxy on :8080
python "$APP_DIR/auth_proxy.py" &
PROXY_PID=$!

# If either process dies, stop the other and exit with the first exit code,
# so Docker's restart policy can recover instead of serving 5xx forever.
wait -n "$BACKEND_PID" "$PROXY_PID"
code=$?
echo "start.sh: a process exited (code $code), stopping container" >&2
stop_all
wait
exit "$code"
