#!/usr/bin/env bash
# Inicia o servidor web ou o worker a partir de qualquer diretorio.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$REPO_DIR/.venv/bin/python"
MODE="${1:-web}"

if [[ ! -x "$PYTHON" ]]; then
    printf 'Ambiente ausente. Execute scripts/install.sh primeiro.\n' >&2
    exit 1
fi

cd "$REPO_DIR"

case "$MODE" in
    web)
        exec "$PYTHON" -m app.web_server
        ;;
    worker)
        exec "$PYTHON" -m app.worker
        ;;
    *)
        printf 'Uso: %s [web|worker]\n' "$0" >&2
        exit 2
        ;;
esac
