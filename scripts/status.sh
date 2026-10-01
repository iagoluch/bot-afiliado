#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$REPO_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    printf 'Ambiente ausente. Execute scripts/install.sh primeiro.\n' >&2
    exit 1
fi

if [[ -f "$REPO_DIR/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] && export "$line"
    done < "$REPO_DIR/.env"
fi

cd "$REPO_DIR"
"$PYTHON" -m app.cli runtime-status

if command -v systemctl >/dev/null 2>&1; then
    systemctl --no-pager status bot-afiliado-worker.service bot-afiliado-web.service 2>/dev/null || true
fi
