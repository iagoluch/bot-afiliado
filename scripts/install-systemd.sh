#!/usr/bin/env bash
# Instala units de sistema que executam a aplicacao com o usuario normal atual.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEMPLATE_DIR="$REPO_DIR/scripts/systemd"

if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
    printf 'Ambiente Python ausente. Execute scripts/install.sh antes de instalar os servicos.\n' >&2
    exit 1
fi

if [[ ! -f "$REPO_DIR/.env" ]]; then
    printf 'Configuracao .env ausente. Execute scripts/install.sh antes de instalar os servicos.\n' >&2
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == "root" ]]; then
        printf 'Execute como usuario normal: sudo scripts/install-systemd.sh\n' >&2
        exit 1
    fi
    RUN_USER="$SUDO_USER"
else
    RUN_USER="$(id -un)"
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
    printf 'Usuario invalido para os servicos: %s\n' "$RUN_USER" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    printf 'Este host nao esta executando systemd; templates validados, unidades nao instaladas.\n' >&2
    exit 1
fi

escape_sed() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

repo_escaped="$(escape_sed "$REPO_DIR")"
user_escaped="$(escape_sed "$RUN_USER")"
for service in bot-afiliado-worker.service bot-afiliado-web.service; do
    template="$TEMPLATE_DIR/$service.in"
    [[ -f "$template" ]] || { printf 'Template ausente: %s\n' "$template" >&2; exit 1; }
    temporary="$(mktemp)"
    trap 'rm -f "$temporary"' EXIT
    sed -e "s|@REPO_DIR@|$repo_escaped|g" -e "s|@RUN_USER@|$user_escaped|g" "$template" > "$temporary"
    sudo install -m 0644 "$temporary" "/etc/systemd/system/$service"
    rm -f "$temporary"
    trap - EXIT
done

sudo systemctl daemon-reload
sudo systemctl enable --now bot-afiliado-worker.service bot-afiliado-web.service
sudo systemctl --no-pager status bot-afiliado-worker.service bot-afiliado-web.service
