#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$REPO_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    printf 'Ambiente ausente. Execute scripts/install.sh primeiro.\n' >&2
    exit 1
fi

cd "$REPO_DIR"
export DRY_RUN=true
exec "$PYTHON" -m pytest -q "$@"
