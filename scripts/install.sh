#!/usr/bin/env bash
# Instala o ambiente local do BOT AFILIADO no Linux sem baixar modelos de IA.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIR="$REPO_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Dependencia ausente: %s\n' "$1" >&2
        return 1
    }
}

require_command "$PYTHON_BIN"
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 ou superior e necessario")
PY

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/requirements-dev.txt"

mkdir -p "$REPO_DIR/data" "$REPO_DIR/data/creatives" "$REPO_DIR/logs"

if [[ ! -f "$REPO_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    chmod 600 "$REPO_DIR/.env"
    printf 'Criado %s com DRY_RUN=true. Ajuste somente quando necessario.\n' "$REPO_DIR/.env"
fi

printf 'IA opcional: configure GEMINI_API_KEY no .env; Granite local permanece desligado por padrao.\n'

if ! require_command ffmpeg; then
    printf 'FFmpeg ausente: videos ficarao com asset pendente; imagens continuam disponiveis.\n' >&2
fi

printf 'Instalacao concluida em %s\n' "$REPO_DIR"
