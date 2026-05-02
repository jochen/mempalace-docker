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
# Sends one file per request to avoid nginx body-size limits.
# Wing name: <hostname>-<decoded-project-label>

set -euo pipefail

ENDPOINT="${MEMPALACE_ENDPOINT:-https://memory.lugrot.de/mine}"
TOKEN="${MEMPALACE_TOKEN:?Please set MEMPALACE_TOKEN}"
PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"
HOSTNAME_PREFIX="$(hostname -s)"
TMPFILE="$(mktemp /tmp/mine-response.XXXXXX.json)"
trap 'rm -f "$TMPFILE"' EXIT

if [[ ! -d "$PROJECTS_DIR" ]]; then
    echo "ERROR: projects dir not found: $PROJECTS_DIR" >&2
    exit 1
fi

ok=0
fail=0

for project_dir in "$PROJECTS_DIR"/*/; do
    [[ -d "$project_dir" ]] || continue

    dir_name=$(basename "$project_dir")
    project_label="${dir_name#-}"
    wing="${HOSTNAME_PREFIX}-${project_label}"

    mapfile -t files < <(find "$project_dir" -maxdepth 1 -name "*.jsonl" -type f 2>/dev/null)

    if [[ ${#files[@]} -eq 0 ]]; then
        echo "SKIP  $wing (no .jsonl files)"
        continue
    fi

    echo "MINE  $wing (${#files[@]} files)..."

    file_ok=0
    file_fail=0

    for f in "${files[@]}"; do
        http_code=$(curl -s -o "$TMPFILE" -w "%{http_code}" \
            -X POST "$ENDPOINT" \
            -H "Authorization: Bearer $TOKEN" \
            -F "wing=$wing" \
            -F "files=@$f")

        if [[ "$http_code" == "200" ]]; then
            (( file_ok++ )) || true
        else
            echo "  ERR [$http_code] $(basename "$f")"
            python3 -m json.tool "$TMPFILE" 2>/dev/null || cat "$TMPFILE"
            (( file_fail++ )) || true
        fi
    done

    if [[ $file_fail -eq 0 ]]; then
        echo "  OK  ${file_ok}/${#files[@]} files"
        (( ok++ )) || true
    else
        echo "  PARTIAL  ${file_ok} ok, ${file_fail} failed"
        (( fail++ )) || true
    fi
done

echo ""
echo "Done — ${ok} projects ok, ${fail} with errors."
