"""Read-only official ML product catalog probe.

The catalog is NOT a marketplace listing or an affiliate offer. Never queue,
publish, or fabricate commission links from these records.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from app import mercadolivre_oauth as oauth

API_URL = "https://api.mercadolibre.com/products/search"
MAX_BODY_BYTES = 512 * 1024
MAX_RESULTS = 10
ALLOWED_IMAGE_HOST = "http2.mlstatic.com"


class CatalogProbeError(RuntimeError):
    pass


def _access_token(token_path: Path, *, now: float | None = None) -> str:
    if not token_path.is_file():
        raise CatalogProbeError("OAuth nao configurado: execute python -m app.mercadolivre_oauth start")
    try:
        record = json.loads(token_path.read_text(encoding="utf-8"))
        expires_at = float(record["expires_at"])
        access_token = str(record["access_token"])
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise CatalogProbeError("Credenciais OAuth locais invalidas") from exc
    current = time.time() if now is None else now
    if expires_at <= current + 300:
        if token_path != oauth.TOKEN_FILE:
            raise CatalogProbeError("Token de teste expirado, renovacao desabilitada")
        try:
            oauth.refresh()
            record = json.loads(token_path.read_text(encoding="utf-8"))
            access_token = str(record["access_token"])
            expires_at = float(record["expires_at"])
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            raise CatalogProbeError("Renovacao de token ML falhou: execute mercadolivre_oauth status") from exc
    if not access_token or expires_at <= current:
        raise CatalogProbeError("Access Token ausente ou expirado")
    return access_token


def search_catalog(
    query: str,
    *,
    limit: int = 5,
    token_path: Path = oauth.TOKEN_FILE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    term = query.strip()
    if not 2 <= len(term) <= 120 or any(ord(ch) < 32 for ch in term):
        raise ValueError("Busca deve conter entre 2 e 120 caracteres validos")
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"Limite de resultados deve ser de 1 a {MAX_RESULTS}")
    token = _access_token(token_path)
    url = API_URL + "?" + urllib.parse.urlencode({
        "site_id": "MLB", "status": "active", "q": term, "limit": limit,
    })
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "BotAfiliado/0.4 catalog-readonly",
    }, method="GET")
    try:
        with opener(request, timeout=15) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        codes = {
            401: "Token recusado: valide e renove o OAuth",
            403: "Acesso negado: escopos/API de catalogo nao liberados",
            404: "Endpoint de catalogo indisponivel",
            429: "Limite de consultas atingido; aguarde antes de tentar novamente",
        }
        raise CatalogProbeError(f"Consulta catalogo ML HTTP {exc.code}: {codes.get(exc.code, 'erro na API')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CatalogProbeError("Falha de rede ao consultar catalogo Mercado Livre") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise CatalogProbeError("Resposta da API excedeu o tamanho permitido")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise CatalogProbeError("Resposta invalida na API de catalogo") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise CatalogProbeError("API nao retornou a estrutura de busca de catalogo esperada")
    records: list[dict[str, Any]] = []
    for p in data["results"][:limit]:
        if not isinstance(p, dict):
            continue
        photos = p.get("pictures")
        image = None
        if isinstance(photos, list):
            for photo in photos:
                if not isinstance(photo, dict):
                    continue
                candidate = photo.get("url")
                if not isinstance(candidate, str):
                    continue
                parsed = urllib.parse.urlsplit(candidate)
                if parsed.scheme == "https" and parsed.hostname == ALLOWED_IMAGE_HOST and not parsed.username and not parsed.password:
                    image = candidate
                    break
        records.append({
            "catalog_product_id": str(p.get("id") or ""),
            "title": str(p.get("name") or "")[:200],
            "domain": str(p.get("domain_id") or ""),
            "image_url": image,
        })
    return {
        "status": "CATALOG_ACCESS_OK",
        "site": "MLB",
        "query": term,
        "results": records,
        "returned": len(records),
        "has_sellable_offer": False,
        "has_affiliate_link": False,
        "may_publish_to_telegram": False,
        "note": "Catalogo de produtos: sem preco comprovado, anuncio vendavel ou link comissionado.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Consulta oficial do catalogo ML, somente leitura")
    parser.add_argument("query", help="Produto tech para pesquisar, ex.: SSD NVMe")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        result = search_catalog(args.query, limit=args.limit)
    except (CatalogProbeError, ValueError) as exc:
        parser.exit(1, f"ERRO: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
