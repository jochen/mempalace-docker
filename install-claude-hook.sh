#!/usr/bin/env bash
# Set up MemPalace for Claude Code — idempotent, safe to re-run.
#
# Configures:
#   1. MCP server in ~/.claude.json        (needs MEMPALACE_TOKEN)
#   2. SessionStart reconnect hook in ~/.claude/settings.json
#
# Usage:
#   MEMPALACE_TOKEN=<token> \
#     curl -sf https://raw.githubusercontent.com/jochen/mempalace-docker/main/install-claude-hook.sh | bash
#
# To update the hook only (no MCP config change), omit MEMPALACE_TOKEN.

set -euo pipefail

MEMPALACE_URL="${MEMPALACE_URL:-https://memory.lugrot.de/mcp}"
CLAUDE_JSON="$HOME/.claude.json"
SETTINGS="$HOME/.claude/settings.json"

mkdir -p "$HOME/.claude"

# --- 1. MCP server config (~/.claude.json) ---
if [[ -n "${MEMPALACE_TOKEN:-}" ]]; then
  python3 - "$CLAUDE_JSON" "$MEMPALACE_URL" "$MEMPALACE_TOKEN" << 'PYEOF'
import json, sys, os

path, url, token = sys.argv[1], sys.argv[2], sys.argv[3]

entry = {
    "type": "http",
    "url": url,
    "headers": {
        "Authorization": f"Bearer {token}"
    }
}

if os.path.exists(path):
    with open(path) as f:
        config = json.load(f)
else:
    config = {}

config.setdefault("mcpServers", {})["mempalace"] = entry

with open(path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print(f"OK: {path}  (mempalace → {url})")
PYEOF
else
  echo "SKIP: MEMPALACE_TOKEN not set — skipping MCP server config"
fi

# --- 2. SessionStart hook (~/.claude/settings.json) ---
python3 - "$SETTINGS" << 'PYEOF'
import json, sys, os

path = sys.argv[1]

new_hook = {
    "hooks": [
        {
            "type": "mcp_tool",
            "server": "mempalace",
            "tool": "mempalace_reconnect",
            "statusMessage": "Reconnecting to MemPalace..."
        }
    ]
}

if os.path.exists(path):
    with open(path) as f:
        settings = json.load(f)
else:
    settings = {}

session_hooks = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
session_hooks[:] = [
    h for h in session_hooks
    if not any(
        t.get("server") == "mempalace" and t.get("tool") == "mempalace_reconnect"
        for t in h.get("hooks", [])
    )
]
session_hooks.append(new_hook)

with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"OK: {path}  (SessionStart hook)")
PYEOF
