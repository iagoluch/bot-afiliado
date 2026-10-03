#!/usr/bin/env bash
# Disposable end-to-end launch smoke test. Never performs external publication.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$REPO_DIR/.venv/bin/python"
SOURCE="$REPO_DIR/examples/shopee_offers.sample.csv"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ ! -x "$PYTHON" ]]; then
    printf 'Ambiente ausente. Execute scripts/install.sh primeiro.\n' >&2
    exit 1
fi

cd "$REPO_DIR"
export DRY_RUN=true
export DATABASE_PATH="$TMP_DIR/affiliate.db"
export CREATIVES_PATH="$TMP_DIR/creatives"
export WORKER_LOCK_PATH="$TMP_DIR/worker.lock"

"$PYTHON" -m app.cli init-db
"$PYTHON" -m app.cli cycle "$SOURCE" --campaign launch-smoke --adapter shopee
"$PYTHON" -m app.cli p1-cycle "$SOURCE" --campaign launch-smoke-p1 --adapter shopee
"$PYTHON" -m app.cli overview
"$PYTHON" -m app.cli social-queue
"$PYTHON" -m app.cli runtime-status
"$PYTHON" -m app.cli events --limit 20

printf 'Launch smoke concluido em banco descartavel; nenhuma publicacao externa foi permitida.\n'
