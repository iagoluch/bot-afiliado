from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def expires_after(value: str | None, hours: int = 24) -> str:
    try:
        base = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base.astimezone(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def money_to_cents(value: str | int | float | Decimal | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace("R$", "").replace(" ", "")
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
    else:
        cleaned = str(value)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"valor monetario invalido: {value!r}") from exc
    if amount < 0:
        raise ValueError("valor monetario nao pode ser negativo")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(slots=True)
class Offer:
    merchant: str
    affiliate_network: str
    external_product_id: str
    title: str
    current_price_cents: int
    source_url: str
    affiliate_url: str
    description: str = ""
    category: str = ""
    brand: str = ""
    original_price_cents: int | None = None
    discount_percent: float | None = None
    coupon: str | None = None
    coupon_expiration: str | None = None
    shipping: str | None = None
    rating: float | None = None
    sales_count: int | None = None
    commission_rate: float | None = None
    commission_estimate_cents: int | None = None
    image_urls: list[str] = field(default_factory=list)
    deeplink: str | None = None
    collected_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    stock_status: str = "UNKNOWN"
    source_type: str = "MANUAL_OFFICIAL_LINK"
    tracking_metadata: dict[str, Any] = field(default_factory=dict)
