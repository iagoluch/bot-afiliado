from __future__ import annotations

import json
from html import escape
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


def _telegram_browser_message(offer: Mapping[str, Any]) -> str:
    """Escaped Telegram HTML, based only on locally verified Chrome facts."""
    from app.services.nvidia_copy import _sanitize_hook
    from app.services.tech_drops import clean_title, creative_copy

    metadata = offer.get("tracking_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    raw_title = str(offer["title"]).strip()
    merchant = str(offer["merchant"]).strip()
    url = str(offer["affiliate_url"]).strip()
    if not url.startswith("https://"):
        raise ValueError("link afiliado HTTPS obrigatorio")

    identity = str(offer.get("external_product_id") or offer.get("source_url") or raw_title)
    heading, phrase = creative_copy(raw_title, identity, _sanitize_hook(metadata.get("copy_hook")))
    discount = verified_discount(offer)
    if discount:
        heading = "📉 PREÇO CAIU!"  # Only when price history verified it.

    # Telegram uses HTML parse_mode for this template. Escape all strings
    # derived from the product page or seller; never trust seller markup.
    # Put the creative hook first for Telegram push notification previews.
    # Keep the advertising disclosure visible at the very end of the post.
    lines = [
        f"<b>{escape(heading)}</b>",
        escape(phrase),
        "",
        f"<b>{escape(clean_title(raw_title))}</b>",
        "",
    ]
    if discount:
        lines.append(f"🏷 De {brl(discount[0])}")

    price = int(offer["current_price_cents"])
    is_pix = (
        metadata.get("price_evidence") == "VISIBLE_DOM_PRICE_V2"
        and metadata.get("price_payment_method") == "PIX"
    )
    shown_price = f"{brl(price)} no Pix" if is_pix else brl(price)
    lines.append(f"💰 <b>{shown_price}</b>")
    if is_pix:
        alternative = metadata.get("other_payment_price_cents")
        if type(alternative) is int and alternative > price:
            lines.append(f"💳 {brl(alternative)} em outros meios de pagamento")

    count = metadata.get("installment_count")
    amount = metadata.get("installment_cents")
    interest_free = metadata.get("installment_interest_free")
    with_interest = metadata.get("installment_with_interest")
    if (
        type(count) is int and 2 <= count <= 24
        and type(amount) is int and 100 <= amount <= 50_000_000
        and type(interest_free) is bool and type(with_interest) is bool
        and not (interest_free and with_interest)
        and price * 0.9 <= count * amount <= price * 2.5
    ):
        suffix = " sem juros" if interest_free else " com juros" if with_interest else ""
        lines.append(f"💳 Parcelamento: {count}x de {brl(amount)}{suffix}")

    if discount:
        lines.append(f"📉 {discount[1]:.0f}% de queda verificada")
    if offer.get("coupon"):
        lines.append(f"🎟 Cupom: {escape(str(offer['coupon']).strip()[:80])}")
    if offer.get("shipping"):
        lines.append(f"📦 {escape(str(offer['shipping']).strip()[:120])}")
    lines.extend(["", f"🏬 {escape(merchant)}", "", "🛒 Confira no Mercado Livre:", escape(url), "", "#publi"])
    return "\n".join(lines)


def telegram_direct_message(offer: Mapping[str, Any]) -> str:
    """Copy direta para Telegram; fatos comerciais sempre determinísticos."""
    if offer.get("source_type") == "ML_BROWSER_VERIFIED":
        return _telegram_browser_message(offer)

    # Keep the manually-reviewed and non-ML paths unchanged.
    discount = verified_discount(offer)
    title = str(offer["title"]).strip()[:180]
    merchant = str(offer["merchant"]).strip()
    url = str(offer["affiliate_url"]).strip()
    if not url.startswith("https://"):
        raise ValueError("link afiliado HTTPS obrigatorio")
    lines = ["#publi", "", "🔥 PREÇO CAIU!" if discount else "🔥 ACHADO TECH!", title, ""]
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
