#!/usr/bin/env bash
# mine-to-palace.sh — Upload Claude conversation files to MemPalace.
#
# Usage:
#   export MEMPALACE_TOKEN=<your-bearer-token>
#   ./mine-to-palace.sh
#
# Optional env vars:
#   MEMPALACE_ENDPOINT  default: https://memory.lugrot.de/mine
#   CLAUDE_PROJECTS_DIR default: ~/.claude/projects
#
# Each subdirectory in CLAUDE_PROJECTS_DIR is sent as a separate request.
# Wing name: <hostname>-<decoded-project-label>
# e.g. herbert-mempalace-docker, herbert-openclaw

set -euo pipefail

ENDPOINT="${MEMPALACE_ENDPOINT:-https://memory.lugrot.de/mine}"
TOKEN="${MEMPALACE_TOKEN:?Please set MEMPALACE_TOKEN}"
PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"
HOSTNAME_PREFIX="$(hostname -s)"

if [[ ! -d "$PROJECTS_DIR" ]]; then
    echo "ERROR: projects dir not found: $PROJECTS_DIR" >&2
    exit 1
fi

ok=0
fail=0

for project_dir in "$PROJECTS_DIR"/*/; do
    [[ -d "$project_dir" ]] || continue

    # Claude encodes project paths as dir names, e.g. "-home-jochen-myproject"
    # Strip the leading dash to get a readable label.
    dir_name=$(basename "$project_dir")
    project_label="${dir_name#-}"
    wing="${HOSTNAME_PREFIX}-${project_label}"

    mapfile -t files < <(find "$project_dir" -maxdepth 1 -name "*.jsonl" -type f 2>/dev/null)

    if [[ ${#files[@]} -eq 0 ]]; then
        echo "SKIP  $wing (no .jsonl files)"
        continue
    fi

    echo "MINE  $wing (${#files[@]} files)..."

    # Build -F file=@path args
    file_args=()
    for f in "${files[@]}"; do
        file_args+=(-F "files=@$f")
    done

    http_code=$(curl -s -o /tmp/mine-response.json -w "%{http_code}" \
        -X POST "$ENDPOINT" \
        -H "Authorization: Bearer $TOKEN" \
        -F "wing=$wing" \
        "${file_args[@]}")

    if [[ "$http_code" == "200" ]]; then
        drawers=$(python3 -c "
import json, sys
try:
    d = json.load(open('/tmp/mine-response.json'))
    print(d.get('stdout','').strip().splitlines()[-1] if d.get('stdout') else 'ok')
except Exception:
    print('ok')
" 2>/dev/null || echo "ok")
        echo "  OK  [$http_code] $drawers"
        (( ok++ )) || true
    else
        echo "  ERR [$http_code] $wing"
        python3 -m json.tool /tmp/mine-response.json 2>/dev/null || cat /tmp/mine-response.json
        (( fail++ )) || true
    fi
done

echo ""
echo "Done — ${ok} ok, ${fail} failed."
rm -f /tmp/mine-response.json
