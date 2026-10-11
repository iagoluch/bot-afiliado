from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit


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


def telegram_direct_message(offer: Mapping[str, Any]) -> str:
    """Copy direta para Telegram; utiliza exclusivamente fatos comprovados."""
    discount = verified_discount(offer)
    title = str(offer["title"]).strip()[:180]
    merchant = str(offer["merchant"]).strip()
    url = str(offer["affiliate_url"]).strip()
    if not url.startswith("https://"):
        raise ValueError("link afiliado HTTPS obrigatorio")
    lines = [
        "#publi", "",
        "🔥 PREÇO CAIU!" if discount else "🔥 ACHADO TECH!",
        title, "",
    ]
    if discount:
        lines.append(f"🏷 De {brl(discount[0])}")
    lines.append(f"💰 {brl(int(offer['current_price_cents']))}")
    if discount:
        lines.append(f"📉 {discount[1]:.0f}% de queda verificada")
    lines.append(f"🏬 {merchant}")
    if offer.get("shipping"):
        lines.append(f"📦 {str(offer['shipping']).strip()[:120]}")
    if offer.get("coupon"):
        lines.append(f"🎟 Cupom: {str(offer['coupon']).strip()[:80]}")
    lines.extend(["", "👇 Confira pelo link oficial:", url])
    return "\n".join(lines)


def telegram_product_photo(offer: Mapping[str, Any]) -> str | None:
    """Only official product-host HTTPS photos, never page scraping."""
    raw: Any = offer.get("image_urls")
    if raw is None:
        raw = offer.get("image_urls_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, list):
        return None
    for entry in raw:
        if not isinstance(entry, str):
            continue
        try:
            parsed = urlsplit(entry)
            host = (parsed.hostname or "").lower()
            safe = (
                parsed.scheme == "https"
                and parsed.port in (None, 443)
                and not parsed.username and not parsed.password
                and not parsed.fragment
                and (host == "http2.mlstatic.com"
                     or host == "susercontent.com"
                     or host.endswith(".susercontent.com"))
            )
        except ValueError:
            safe = False
        if safe:
            return entry
    return None
