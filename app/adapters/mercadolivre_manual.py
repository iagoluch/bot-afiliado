from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.adapters.base import AffiliateAdapter, Capability, CapabilityStatus
from app.adapters.local_files import read_local_records
from app.models import Offer, expires_after, money_to_cents, utc_now


class MercadoLivreManualAdapter(AffiliateAdapter):
    name = "mercadolivre_manual_official_link"
    integration_status = CapabilityStatus.MANUAL_OR_PENDING
    capabilities = {
        Capability.DISCOVER_PRODUCTS: CapabilityStatus.MANUAL,
        Capability.DISCOVER_DEALS: CapabilityStatus.MANUAL,
        Capability.GET_PRICE: CapabilityStatus.MANUAL,
        Capability.GET_COUPONS: CapabilityStatus.MANUAL,
        Capability.GET_COMMISSION: CapabilityStatus.MANUAL,
        Capability.CREATE_AFFILIATE_LINK: CapabilityStatus.MANUAL,
        Capability.GET_CONVERSIONS: CapabilityStatus.MANUAL,
        Capability.GET_REPORTS: CapabilityStatus.MANUAL,
        Capability.GET_CREATIVES: CapabilityStatus.MANUAL_OR_PENDING,
    }

    def import_offers(self, path: Path) -> list[Offer]:
        records = read_local_records(path, label="Mercado Livre")
        if not records:
            raise ValueError("arquivo Mercado Livre nao contem ofertas")
        return [self._normalize(record) for record in records]

    def _normalize(self, record: dict[str, Any]) -> Offer:
        required = ("external_product_id", "title", "current_price", "source_url", "affiliate_url")
        missing = [field for field in required if not str(record.get(field, "")).strip()]
        if missing:
            raise ValueError(f"campos obrigatorios ausentes: {', '.join(missing)}")
        source_url = str(record["source_url"]).strip()
        affiliate_url = str(record["affiliate_url"]).strip()
        self._validate_url(source_url, "source_url", allow_short=False)
        self._validate_url(affiliate_url, "affiliate_url", allow_short=True)
        current = money_to_cents(record["current_price"])
        unverified_reference = money_to_cents(record.get("original_price"))
        if not current:
            raise ValueError("current_price Mercado Livre precisa ser positivo")
        tracking_metadata: dict[str, Any] = {"official_link_preserved": True}
        if unverified_reference is not None:
            tracking_metadata["unverified_reference_price_cents"] = unverified_reference
        collected_at = str(record.get("collected_at") or utc_now())
        return Offer(
            merchant="Mercado Livre",
            affiliate_network="Mercado Livre Afiliados e Criadores",
            external_product_id=str(record["external_product_id"]).strip(),
            title=str(record["title"]).strip(),
            description=str(record.get("description") or "").strip(),
            category=str(record.get("category") or "").strip(),
            brand=str(record.get("brand") or "").strip(),
            original_price_cents=None,
            current_price_cents=current,
            discount_percent=None,
            coupon=str(record.get("coupon") or "").strip() or None,
            coupon_expiration=str(record.get("coupon_expiration") or "").strip() or None,
            shipping=str(record.get("shipping") or "").strip() or None,
            rating=self._optional_float(record.get("rating")),
            sales_count=self._optional_int(record.get("sales_count")),
            commission_rate=self._optional_float(record.get("commission_rate")),
            image_urls=self._split_list(record.get("image_urls")),
            source_url=source_url,
            affiliate_url=affiliate_url,
            deeplink=affiliate_url,
            collected_at=collected_at,
            expires_at=str(record.get("expires_at") or "").strip() or expires_after(collected_at),
            stock_status=str(record.get("stock_status") or "UNKNOWN").strip().upper(),
            source_type="MANUAL_OFFICIAL_LINK",
            tracking_metadata=tracking_metadata,
        )

    @staticmethod
    def _validate_url(value: str, field: str, *, allow_short: bool) -> None:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        allowed = host in {"mercadolivre.com", "www.mercadolivre.com", "mercadolivre.com.br", "www.mercadolivre.com.br"} or host.endswith(".mercadolivre.com.br")
        path = parsed.path.lower()
        is_short = host in {"mercadolivre.com", "www.mercadolivre.com"} and path.startswith("/sec/")
        is_product = host.startswith("produto.") or "/mlb-" in path or path.startswith("/p/mlb")
        if parsed.scheme != "https" or not allowed or not (is_product or (allow_short and is_short)):
            raise ValueError(f"{field} deve ser URL HTTPS oficial do Mercado Livre")

    @staticmethod
    def _split_list(value: Any) -> list[str]:
        if not value:
            return []
        values = value if isinstance(value, list) else str(value).split("|")
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
