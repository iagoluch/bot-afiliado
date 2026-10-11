from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, TextIO

from app.adapters.mercadolivre_manual import MercadoLivreManualAdapter
from app.adapters.shopee_manual import ShopeeManualAdapter
from app.config import Settings
from app.models import Offer


MAX_ASSISTED_JSON_BYTES = 1024 * 1024
MAX_ASSISTED_OFFERS = 500


@dataclass(frozen=True)
class AssistedBatch:
    offers: list[Offer]
    rejected_non_technology: int
    source_key: str


def read_assisted_json(
    stream: TextIO,
    *,
    max_bytes: int = MAX_ASSISTED_JSON_BYTES,
) -> list[dict[str, Any]]:
    raw = stream.read(max_bytes + 1)
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError(f"entrada JSON excede limite de {max_bytes} bytes")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("entrada JSON invalida") from exc
    records = (
        payload
        if isinstance(payload, list)
        else payload.get("offers") if isinstance(payload, dict) else None
    )
    if not isinstance(records, list):
        raise ValueError("entrada JSON deve ser uma lista ou objeto com offers")
    if not records:
        raise ValueError("entrada JSON nao contem ofertas")
    if len(records) > MAX_ASSISTED_OFFERS:
        raise ValueError(f"entrada JSON excede limite de {MAX_ASSISTED_OFFERS} ofertas")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("entrada JSON contem oferta invalida")
    return [dict(record) for record in records]


def _search_text(offer: Offer) -> str:
    raw = " ".join((offer.title, offer.category, offer.brand)).lower()
    return "".join(
        character for character in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(character)
    )


def _normalized_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for term in terms:
        value = "".join(
            character for character in unicodedata.normalize("NFKD", term.strip().lower())
            if not unicodedata.combining(character)
        )
        if value:
            normalized.append(value)
    if not normalized:
        raise ValueError("filtro de tecnologia precisa conter ao menos um termo")
    return tuple(dict.fromkeys(normalized))


def prepare_assisted_offers(
    adapter_name: str,
    records: list[dict[str, Any]],
    *,
    terms: tuple[str, ...],
) -> AssistedBatch:
    for index, record in enumerate(records, start=1):
        if not str(record.get("collected_at") or "").strip():
            raise ValueError(f"oferta {index}: collected_at e obrigatorio na entrada assistida")
    normalized_name = adapter_name.strip().lower()
    if normalized_name == "shopee":
        offers = ShopeeManualAdapter().import_records(records)
    elif normalized_name in {"mercadolivre", "mercado-livre", "ml"}:
        offers = MercadoLivreManualAdapter().import_records(records)
    else:
        raise ValueError(f"adapter assistido desconhecido: {adapter_name}")

    accepted: list[Offer] = []
    normalized_terms = _normalized_terms(terms)
    for offer in offers:
        text = _search_text(offer)
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) for term in normalized_terms):
            accepted.append(offer)
    return AssistedBatch(
        offers=accepted,
        rejected_non_technology=len(offers) - len(accepted),
        source_key=hashlib.sha256(
            json.dumps(
                [asdict(offer) for offer in accepted],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )


def collection_status(settings: Settings) -> dict[str, dict[str, Any]]:
    shopee_credentials = bool(settings.shopee_app_id and settings.shopee_secret)
    return {
        "shopee": {
            "status": (
                "BLOCKED_UNVERIFIED_CREDENTIALS"
                if shopee_credentials
                else "BLOCKED_MISSING_CREDENTIALS"
            ),
            "credentials_present": shopee_credentials,
            "automatic_connector_enabled": False,
            "network_calls": 0,
            "fallback": "add-offer or assisted-import JSON via stdin",
        },
        "mercadolivre": {
            "status": "BLOCKED_UNVERIFIED_AFFILIATE_CONTRACT",
            "credentials_present": None,
            "automatic_connector_enabled": False,
            "network_calls": 0,
            "fallback": "add-offer or assisted-import JSON via stdin",
        },
    }
