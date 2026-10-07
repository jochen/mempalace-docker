"""
Readiness probe for MemPalace's native HTTP transport on 127.0.0.1:8081.

/healthz only proves the HTTP server is up (it answers before Chroma or the
embedding model are loaded), so this also runs initialize + mempalace_status
over JSON-RPC. Exit 0 when the backend answers the tool call, 1 otherwise.
Used by start.sh before the public auth proxy is started.
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8081"


def _rpc(payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        BASE + "/mcp",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def main() -> int:
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=2) as response:
            if response.status != 200:
                return 1
        _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "backend_ready", "version": "1"},
        }}, timeout=10)
        reply = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "mempalace_status", "arguments": {}}},
                     timeout=120)
    except (OSError, ValueError):
        return 1
    return 0 if "result" in reply else 1


if __name__ == "__main__":
    sys.exit(main())
