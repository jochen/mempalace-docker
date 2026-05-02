#!/usr/bin/env bash
# Install the MemPalace SessionStart hook into ~/.claude/settings.json.
# Safe to run multiple times — idempotent, merges with existing settings.
#
# Usage:
#   curl -sf https://raw.githubusercontent.com/jochen/mempalace-docker/main/install-claude-hook.sh | bash

set -euo pipefail

SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"

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

# Load existing settings or start empty
if os.path.exists(path):
    with open(path) as f:
        settings = json.load(f)
else:
    settings = {}

# Merge: remove any existing mempalace_reconnect hook, then add fresh
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

print(f"OK: {path}")
PYEOF
