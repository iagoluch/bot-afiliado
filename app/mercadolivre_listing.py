"""Inspecao somente leitura do anuncio vencedor de um produto catalogado no ML.

GET /products/{id} identifica buy_box_winner; GET /items/{item_id} verifica
anuncio, preco, estoque e URL de produto. Nenhuma URL e comissionada aqui:
esta ferramenta NUNCA cadastra ofertas ou publica no Telegram.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from app import mercadolivre_oauth as oauth
from app.mercadolivre_catalog import CatalogProbeError, _access_token

BASE_URL = "https://api.mercadolibre.com"
MAX_RESPONSE_BYTES = 512 * 1024
MAX_IDS = 5
ITEM_ID = re.compile(r"MLB[0-9]{5,16}\Z")
PERMALINK_HOSTS = ("mercadolivre.com.br", "mercadolivre.com", "mercadolibre.com.br")
PICTURE_HOST = "http2.mlstatic.com"


def _official_https(value: Any, hosts: tuple[str, ...]) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return None
        if any(host == h or host.endswith("." + h) for h in hosts):
            return value
    except ValueError:
        pass
    return None


def _read_endpoint(
    path: str,
    *,
    token: str,
    opener: Callable[..., Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "BotAfiliado/0.4 listing-readonly",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=15) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        explanations = {
            401: "token negado; conferir OAuth",
            403: "sem permissao para esse recurso",
            404: "produto ou anuncio nao localizado",
            429: "limite de consultas; aguarde",
        }
        raise CatalogProbeError(
            f"Mercado Livre HTTP {exc.code}: {explanations.get(exc.code, 'consulta falhou')}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CatalogProbeError("Falha de rede ao consultar anuncios") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise CatalogProbeError("Resposta excedeu tamanho permitido")
    try:
        payload = json.loads(data)
    except (UnicodeError, ValueError) as exc:
        raise CatalogProbeError("Resposta JSON invalida") from exc
    if not isinstance(payload, dict):
        raise CatalogProbeError("Estrutura de resposta inesperada")
    return payload


def _price_cents(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        amount = Decimal(str(value))
        cents = amount * 100
        if amount <= 0 or not amount.is_finite() or cents != cents.to_integral_value():
            return None
        return int(cents)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _original_photo(item: dict[str, Any]) -> str | None:
    pictures = item.get("pictures")
    if isinstance(pictures, list):
        for image in pictures:
            if isinstance(image, dict):
                for key in ("secure_url", "url"):
                    found = _official_https(image.get(key), (PICTURE_HOST,))
                    if found:
                        return found
    return _official_https(item.get("secure_thumbnail"), (PICTURE_HOST,))


def inspect_listing(
    catalog_product_id: str,
    *,
    token_path: Path = oauth.TOKEN_FILE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    if not ITEM_ID.fullmatch(catalog_product_id):
        raise ValueError("ID de catalogo invalido: esperado MLB seguido de digitos")
    token = _access_token(token_path)
    product = _read_endpoint(
        f"/products/{catalog_product_id}", token=token, opener=opener
    )
    if product.get("id") != catalog_product_id:
        raise CatalogProbeError("API retornou ID de produto divergente")
    result: dict[str, Any] = {
        "catalog_product_id": catalog_product_id,
        "catalog_status": product.get("status"),
        "product_title": str(product.get("name") or "")[:200],
        "status": "NO_ACTIVE_LISTING",
        "has_sellable_listing": False,
        "has_affiliate_link": False,
        "may_publish_to_telegram": False,
    }
    if product.get("status") != "active":
        return result
    winner = product.get("buy_box_winner")
    if not isinstance(winner, dict):
        return result
    item_id = str(winner.get("item_id") or "")
    if not ITEM_ID.fullmatch(item_id):
        return result

    item = _read_endpoint(f"/items/{item_id}", token=token, opener=opener)
    if item.get("id") != item_id or item.get("site_id") != "MLB":
        raise CatalogProbeError("API retornou anuncio divergente")
    if item.get("catalog_product_id") not in (None, catalog_product_id):
        raise CatalogProbeError("Anuncio nao pertence ao produto consultado")
    try:
        quantity = int(item.get("available_quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    cents = _price_cents(item.get("price"))
    permalink = _official_https(item.get("permalink"), PERMALINK_HOSTS)
    if (item.get("status") != "active" or item.get("currency_id") != "BRL"
            or quantity <= 0 or cents is None or permalink is None):
        return result
    result.update({
        "status": "LISTING_VERIFIED",
        "has_sellable_listing": True,
        "item_id": item_id,
        "title": str(item.get("title") or "")[:200],
        "price_brl": f"{Decimal(cents) / 100:.2f}",
        "available_quantity": quantity,
        "seller_id": item.get("seller_id"),
        "product_url": permalink,
        "image_url": _original_photo(item),
        "note": "URL comum, nao comissionada. Gerar/validar link de afiliado oficial antes de publicar.",
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verificar anuncio ganhador, preco e estoque de produto catalogado MLB"
    )
    parser.add_argument("catalog_ids", nargs="+", help="Um a cinco ids MLB do catalogo")
    args = parser.parse_args()
    if len(args.catalog_ids) > MAX_IDS:
        parser.error(f"Consulte no maximo {MAX_IDS} produtos por vez")
    try:
        records = [inspect_listing(id) for id in args.catalog_ids]
    except (CatalogProbeError, ValueError) as exc:
        parser.exit(1, f"ERRO: {exc}\n")
    print(json.dumps({
        "status": "READ_ONLY_INSPECTION",
        "results": records,
        "has_affiliate_links": False,
        "may_publish_to_telegram": False,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
