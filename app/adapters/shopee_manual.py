from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.adapters.base import AffiliateAdapter, Capability, CapabilityStatus
from app.models import Offer, expires_after, money_to_cents, utc_now


class ShopeeManualAdapter(AffiliateAdapter):
    """Importa ofertas com link criado pelos meios oficiais da Shopee.

    O contrato autenticado da Affiliate API brasileira nao e presumido aqui.
    A URL afiliada recebida e validada e persistida sem qualquer alteracao.
    """

    name = "shopee_manual_official_link"
    integration_status = CapabilityStatus.MANUAL_OR_PENDING
    capabilities = {
        Capability.DISCOVER_PRODUCTS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.DISCOVER_DEALS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.GET_PRICE: CapabilityStatus.MANUAL,
        Capability.GET_COUPONS: CapabilityStatus.MANUAL,
        Capability.GET_COMMISSION: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.CREATE_AFFILIATE_LINK: CapabilityStatus.MANUAL,
        Capability.GET_CONVERSIONS: CapabilityStatus.MANUAL,
        Capability.GET_REPORTS: CapabilityStatus.MANUAL,
        Capability.GET_CREATIVES: CapabilityStatus.MANUAL,
    }

    def import_offers(self, path: Path) -> list[Offer]:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                records = list(csv.DictReader(handle))
        elif suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else data.get("offers", [])
        else:
            raise ValueError("formato aceito: .csv ou .json")
        if not records:
            raise ValueError("arquivo nao contem ofertas")
        return [self._normalize(record) for record in records]

    def _normalize(self, record: dict[str, Any]) -> Offer:
        required = ("external_product_id", "title", "current_price", "source_url", "affiliate_url")
        missing = [field for field in required if not str(record.get(field, "")).strip()]
        if missing:
            raise ValueError(f"campos obrigatorios ausentes: {', '.join(missing)}")
        source_url = str(record["source_url"]).strip()
        affiliate_url = str(record["affiliate_url"]).strip()
        self._validate_shopee_url(source_url, "source_url")
        self._validate_shopee_url(affiliate_url, "affiliate_url")
        current = money_to_cents(record["current_price"])
        unverified_reference = money_to_cents(record.get("original_price"))
        images = self._split_list(record.get("image_urls"))
        parsed_query = parse_qs(urlsplit(affiliate_url).query)
        sub_id = next((parsed_query[key][0] for key in ("sub_id", "subid", "subId") if parsed_query.get(key)), None)
        tracking_metadata: dict[str, Any] = {}
        if sub_id:
            tracking_metadata["sub_id_from_official_link"] = sub_id
        if unverified_reference is not None:
            tracking_metadata["unverified_reference_price_cents"] = unverified_reference
        collected_at = str(record.get("collected_at") or utc_now())
        return Offer(
            merchant="Shopee",
            affiliate_network="Shopee Afiliados",
            external_product_id=str(record["external_product_id"]).strip(),
            title=str(record["title"]).strip(),
            description=str(record.get("description") or "").strip(),
            category=str(record.get("category") or "").strip(),
            brand=str(record.get("brand") or "").strip(),
            original_price_cents=None,
            current_price_cents=current or 0,
            discount_percent=None,
            coupon=str(record.get("coupon") or "").strip() or None,
            coupon_expiration=str(record.get("coupon_expiration") or "").strip() or None,
            shipping=str(record.get("shipping") or "").strip() or None,
            rating=self._optional_float(record.get("rating")),
            sales_count=self._optional_int(record.get("sales_count")),
            commission_rate=self._optional_float(record.get("commission_rate")),
            image_urls=images,
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
    def _validate_shopee_url(value: str, field: str) -> None:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        allowed = host == "shope.ee" or host.endswith(".shopee.com.br") or host == "shopee.com.br"
        if parsed.scheme != "https" or not allowed:
            raise ValueError(f"{field} deve ser URL HTTPS oficial da Shopee")

    @staticmethod
    def _split_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split("|") if item.strip()]

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
