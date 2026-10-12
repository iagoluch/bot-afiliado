"""OAuth manual Mercado Livre, sem servidor público e sem exposição de secrets.

Uso: python -m app.mercadolivre_oauth start | finish | status | refresh
A GitHub Pages serve apenas para devolver code e state para inserção local.
Não habilita, por si só, API de afiliados ou geração de links comissionados.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT, load_project_env

AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
PENDING_FILE = PROJECT_ROOT / "data" / "mercadolivre_oauth_pending.json"
TOKEN_FILE = PROJECT_ROOT / "data" / "mercadolivre_oauth_tokens.json"
MAX_TOKEN_RESPONSE_BYTES = 16 * 1024
PENDING_TTL_SECONDS = 900


def _app_config(*, need_secret: bool = False) -> tuple[str, str, str, bool]:
    load_project_env()
    app_id = os.getenv("MERCADOLIVRE_APP_ID", "").strip()
    secret = os.getenv("MERCADOLIVRE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("MERCADOLIVRE_REDIRECT_URI", "").strip()
    use_pkce = os.getenv("MERCADOLIVRE_PKCE_ENABLED", "false").lower() in ("true", "1", "yes")
    if not app_id.isdigit() or (need_secret and not secret):
        raise ValueError("Configure MERCADOLIVRE_APP_ID e MERCADOLIVRE_CLIENT_SECRET no .env local")
    parsed = urllib.parse.urlsplit(redirect)
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("MERCADOLIVRE_REDIRECT_URI exige HTTPS fixo, sem query/fragment, identico ao DevCenter")
    return app_id, secret, redirect, use_pkce


def _atomic_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".ml-oauth-", suffix=".tmp", delete=False,
        ) as tmp:
            tmp_name = tmp.name
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:
                pass
            json.dump(value, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)


def _private_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Arquivo de credenciais local invalido")
    return data


def authorization_link(app_id: str, redirect: str, state: str, verifier: str = "") -> str:
    query = {
        "response_type": "code",
        "client_id": app_id,
        "redirect_uri": redirect,
        "state": state,
    }
    if verifier:
        challenge = hashlib.sha256(verifier.encode("ascii")).digest()
        query["code_challenge"] = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
        query["code_challenge_method"] = "S256"
    return AUTH_URL + "?" + urllib.parse.urlencode(query)


def start(*, pending_path: Path = PENDING_FILE) -> str:
    app_id, _, redirect, use_pkce = _app_config()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48) if use_pkce else ""
    _atomic_private_json(pending_path, {
        "state": state, "redirect_uri": redirect,
        "app_id": app_id, "verifier": verifier,
        "created_at": time.time(),
    })
    return authorization_link(app_id, redirect, state, verifier)


def _exchange(
    data: dict[str, str],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with opener(request, timeout=20) as response:
            raw = response.read(MAX_TOKEN_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Mercado Livre recusou OAuth (HTTP {exc.code}); confira a configuracao da aplicacao") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Falha de rede OAuth; confira a conexao antes de nova tentativa") from exc
    if len(raw) > MAX_TOKEN_RESPONSE_BYTES:
        raise RuntimeError("Resposta OAuth excedeu tamanho permitido")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Resposta OAuth invalida") from exc
    if not isinstance(payload, dict) or not payload.get("access_token") or not payload.get("refresh_token"):
        raise RuntimeError("Mercado Livre nao retornou tokens validos")
    return payload


def _store_tokens(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    seconds = int(payload.get("expires_in") or 0)
    if seconds <= 0:
        raise ValueError("Resposta OAuth sem validade")
    out = {
        "access_token": str(payload["access_token"]),
        "refresh_token": str(payload["refresh_token"]),
        "expires_at": time.time() + seconds,
        "user_id": payload.get("user_id"),
        "scope": payload.get("scope"),
    }
    _atomic_private_json(path, out)
    return {"connected": True, "expires_in_seconds": seconds, "user_id": out["user_id"]}


def finish(
    code: str,
    state: str,
    *,
    pending_path: Path = PENDING_FILE,
    token_path: Path = TOKEN_FILE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    app_id, secret, redirect, _ = _app_config(need_secret=True)
    try:
        pending = _private_json(pending_path)
    except FileNotFoundError as exc:
        raise ValueError("Inicie com 'start' no mesmo Acer antes de autorizar") from exc
    if (not state or not hmac.compare_digest(str(pending.get("state", "")), state)
            or pending.get("redirect_uri") != redirect or pending.get("app_id") != app_id):
        raise ValueError("State ou configuracao divergente: reinicie a autorizacao (nao troque o codigo)")
    if time.time() - float(pending.get("created_at", 0)) > PENDING_TTL_SECONDS:
        raise ValueError("Autorizacao antiga; execute 'start' novamente")
    if not code or len(code) > 2048 or any(c.isspace() for c in code):
        raise ValueError("Codigo OAuth ausente ou invalido")
    params = {
        "grant_type": "authorization_code",
        "client_id": app_id, "client_secret": secret,
        "code": code, "redirect_uri": redirect,
    }
    if pending.get("verifier"):
        params["code_verifier"] = str(pending["verifier"])
    response = _exchange(params, opener=opener)
    result = _store_tokens(response, token_path)
    pending_path.unlink(missing_ok=True)
    return result


def refresh(
    *,
    token_path: Path = TOKEN_FILE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    app_id, secret, _, _ = _app_config(need_secret=True)
    current = _private_json(token_path)
    # Avoid unnecessarily consuming one-time refresh tokens.
    seconds_left = float(current.get("expires_at", 0)) - time.time()
    if seconds_left > 300:
        return {"connected": True, "expires_in_seconds": int(seconds_left), "refreshed": False}
    response = _exchange({
        "grant_type": "refresh_token",
        "client_id": app_id, "client_secret": secret,
        "refresh_token": str(current["refresh_token"]),
    }, opener=opener)
    result = _store_tokens(response, token_path)
    result["refreshed"] = True
    return result


def status(*, token_path: Path = TOKEN_FILE) -> dict[str, Any]:
    if not token_path.is_file():
        return {"connected": False}
    record = _private_json(token_path)
    return {
        "connected": True,
        "access_token_valid": float(record.get("expires_at", 0)) > time.time(),
        "expires_in_seconds": max(0, int(float(record.get("expires_at", 0)) - time.time())),
        "user_id": record.get("user_id"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Autorizacao OAuth local do Mercado Livre")
    parser.add_argument("action", choices=("start", "finish", "status", "refresh"))
    args = parser.parse_args()
    if args.action == "start":
        print("Abra este endereco no navegador e autorize a aplicacao:")
        print(start())
        print("Depois copie 'code' e 'state' da pagina de retorno e rode: python -m app.mercadolivre_oauth finish")
    elif args.action == "finish":
        code = getpass.getpass("Cole o codigo OAuth (nao aparecera na tela): ").strip()
        state = getpass.getpass("Cole o state (nao aparecera na tela): ").strip()
        print(json.dumps(finish(code, state), ensure_ascii=False))
    elif args.action == "refresh":
        print(json.dumps(refresh(), ensure_ascii=False))
    else:
        print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()
