"""Consulta candidatos de anuncios em /products/{id}/items sem depender da buy box.

Somente leitura; nao gera links comissionados, nao enfileira ou publica.
Endpoint documentado historicamente; acesso atual varia por aplicativo.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from app import mercadolivre_oauth as oauth
from app.mercadolivre_catalog import CatalogProbeError, _access_token
from app.mercadolivre_listing import _read_endpoint, _price_cents

PRODUCT_ID = re.compile(r"MLB[0-9]{5,16}\Z")
MAX_CANDIDATES = 10


def candidates(
    catalog_id: str,
    *,
    limit: int = 5,
    token_path: Path = oauth.TOKEN_FILE,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    if not PRODUCT_ID.fullmatch(catalog_id):
        raise ValueError("Catalog ID invalido (esperado MLB seguido de digitos)")
    if not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("limit precisa estar entre 1 e 10")
    token = _access_token(token_path)
    payload = _read_endpoint(
        f"/products/{catalog_id}/items", token=token, opener=opener,
    )
    if not isinstance(payload.get("results"), list):
        raise CatalogProbeError("Estrutura da lista de anuncios indisponivel")
    rows: list[dict[str, Any]] = []
    for item in payload["results"][:100]:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        if not PRODUCT_ID.fullmatch(item_id):
            continue
        if item.get("currency_id") != "BRL":
            continue
        amount = _price_cents(item.get("price"))
        if amount is None:
            continue
        try:
            quantity = int(item.get("available_quantity") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if quantity <= 0 or quantity > 10**9:
            continue
        rows.append({
            "item_id": item_id,
            "price_brl": str(Decimal(amount) / 100),
            "available_quantity_reported": quantity,
            "seller_id": item.get("seller_id"),
            "price_verified_at_publication": False,
            "affiliate_url": None,
        })
    rows.sort(key=lambda row: (Decimal(row["price_brl"]), row["item_id"]))
    rows = rows[:limit]
    return {
        "status": "CANDIDATES_FOUND" if rows else "NO_SELLABLE_CANDIDATES",
        "catalog_product_id": catalog_id,
        "results": rows,
        "has_affiliate_links": False,
        "may_publish_to_telegram": False,
        "next_step": "Revalidar anuncio e gerar link no Gerador oficial de Afiliados do Mercado Livre.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Encontrar anuncios associados a um produto de catalogo (somente leitura)"
    )
    parser.add_argument("catalog_product_id")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        value = candidates(args.catalog_product_id, limit=args.limit)
    except (ValueError, CatalogProbeError) as exc:
        parser.exit(1, f"ERRO: {exc}\n")
    print(json.dumps(value, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
