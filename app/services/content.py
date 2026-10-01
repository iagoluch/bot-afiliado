from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode


def brl(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def verified_discount(offer: Mapping[str, Any]) -> tuple[int, float] | None:
    """Retorna desconto publicável somente com prova explícita de histórico.

    Campos canônicos de preço anterior/desconto não são prova por si só. O
    produtor precisa anexar a verificação calculada a partir do histórico de
    preços antes de chamar o gerador de conteúdo.
    """
    metadata: Any = offer.get("tracking_metadata")
    if metadata is None:
        try:
            metadata = json.loads(str(offer.get("tracking_metadata_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(metadata, Mapping):
        return None
    verification = metadata.get("discount_verification")
    if not isinstance(verification, Mapping) or verification.get("method") != "PRICE_HISTORY":
        return None
    try:
        current = int(offer["current_price_cents"])
        reference = int(verification["reference_price_cents"])
    except (KeyError, TypeError, ValueError):
        return None
    if current <= 0 or reference <= current:
        return None
    percent = round((reference - current) / reference * 100, 2)
    return reference, percent


def telegram_message(offer: Mapping[str, Any], tracking_url: str) -> str:
    discount = verified_discount(offer)
    headline = "🔥 BAIXOU!" if discount else "🔥 OFERTA"
    lines = ["#publi", "", headline, "", str(offer["title"]), ""]
    if discount:
        lines.append(f"De {brl(discount[0])}")
    lines.append(f"por {brl(offer['current_price_cents'])}")
    if discount:
        lines.extend(["", f"🏷 {discount[1]:.0f}% OFF"])
    if offer.get("coupon"):
        lines.append(f"🎟 Cupom: {offer['coupon']}")
    if offer.get("shipping"):
        lines.append(f"🚚 {offer['shipping']}")
    lines.extend(["", "🛒 Conferir oferta:", tracking_url])
    return "\n".join(lines)


def telegram_creative_id(offer_id: int) -> str:
    return f"telegram-template-v1-{offer_id}"


def telegram_tracking_url(
    public_base_url: str, offer_id: int, campaign_id: str, publication_key: str | None = None,
) -> str:
    creative_id = telegram_creative_id(offer_id)
    parameters = {"channel": "telegram", "campaign_id": campaign_id, "creative_id": creative_id}
    if publication_key is not None:
        parameters["publication_key"] = publication_key
    query = urlencode(parameters)
    return f"{public_base_url.rstrip('/')}/go/{offer_id}?{query}"
