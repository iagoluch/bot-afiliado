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

# Exporta pares KEY=VALUE literalmente, sem executar conteudo de .env como shell.
if [[ -f "$REPO_DIR/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            export "$line"
        else
            printf 'Ignorando linha invalida em .env\n' >&2
        fi
    done < "$REPO_DIR/.env"
fi

export DRY_RUN="${DRY_RUN:-true}"
export DATABASE_PATH="${DATABASE_PATH:-data/affiliate.db}"
cd "$REPO_DIR"
"$PYTHON" -m app.cli init-db

case "$MODE" in
    web)
        exec "$PYTHON" -m uvicorn app.web:app --host "${WEB_HOST:-127.0.0.1}" --port "${WEB_PORT:-8000}"
        ;;
    worker)
        exec "$PYTHON" -m app.worker
        ;;
    *)
        printf 'Uso: %s [web|worker]\n' "$0" >&2
        exit 2
        ;;
esac
