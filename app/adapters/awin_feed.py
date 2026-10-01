from __future__ import annotations

import csv
import gzip
from pathlib import Path
from typing import Any, Iterable, TextIO
from urllib.parse import urlsplit

from app.adapters.base import AffiliateAdapter, Capability, CapabilityStatus
from app.models import Offer, expires_after, money_to_cents, utc_now


MAX_COMPRESSED_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ROWS = 50_000
MAX_FIELD_BYTES = 1_000_000


class _LimitedLines:
    def __init__(self, handle: TextIO, max_bytes: int):
        self.handle = handle
        self.max_bytes = max_bytes
        self.total = 0

    def __iter__(self) -> Iterable[str]:
        for line in self.handle:
            self.total += len(line.encode("utf-8"))
            if self.total > self.max_bytes:
                raise ValueError(f"feed Awin descompactado excede limite de {self.max_bytes} bytes")
            yield line


class AwinFeedAdapter(AffiliateAdapter):
    name = "awin_official_product_feed"
    integration_status = CapabilityStatus.SUPPORTED
    capabilities = {
        Capability.DISCOVER_PRODUCTS: CapabilityStatus.SUPPORTED,
        Capability.DISCOVER_DEALS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_PRICE: CapabilityStatus.SUPPORTED,
        Capability.GET_COUPONS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_COMMISSION: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.CREATE_AFFILIATE_LINK: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_CONVERSIONS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.GET_REPORTS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.GET_CREATIVES: CapabilityStatus.SUPPORTED,
    }

    def __init__(
        self,
        *,
        allowed_affiliate_hosts: tuple[str, ...] = (),
        max_compressed_bytes: int = MAX_COMPRESSED_BYTES,
        max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
        max_rows: int = MAX_ROWS,
        delimiter: str = ",",
    ):
        if len(delimiter) != 1:
            raise ValueError("delimiter Awin deve ter um caractere")
        self.allowed_affiliate_hosts = tuple(host.lower().strip(".") for host in allowed_affiliate_hosts if host)
        self.max_compressed_bytes = max_compressed_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_rows = max_rows
        self.delimiter = delimiter

    def import_offers(self, path: Path) -> list[Offer]:
        is_gzip = path.suffix.lower() == ".gz"
        file_limit = self.max_compressed_bytes if is_gzip else self.max_uncompressed_bytes
        if path.stat().st_size > file_limit:
            kind = "comprimido" if is_gzip else "CSV"
            raise ValueError(f"arquivo Awin {kind} excede limite de {file_limit} bytes")
        opener = gzip.open if is_gzip else open
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_FIELD_BYTES)
        try:
            with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(_LimitedLines(handle, self.max_uncompressed_bytes), delimiter=self.delimiter)
                required = {"aw_deep_link", "product_name", "aw_product_id", "merchant_name", "merchant_id", "search_price", "currency"}
                missing = required - set(reader.fieldnames or [])
                if missing:
                    raise ValueError(f"feed Awin sem colunas obrigatorias: {', '.join(sorted(missing))}")
                offers: list[Offer] = []
                for row_number, row in enumerate(reader, start=2):
                    if len(offers) >= self.max_rows:
                        raise ValueError(f"feed Awin excede limite de {self.max_rows} linhas")
                    offers.append(self._normalize(row, row_number))
        finally:
            csv.field_size_limit(previous_limit)
        if not offers:
            raise ValueError("feed Awin nao contem produtos")
        return offers

    def _normalize(self, row: dict[str, Any], row_number: int) -> Offer:
        currency = str(row.get("currency") or "").strip().upper()
        if currency != "BRL":
            raise ValueError(f"linha {row_number}: moeda {currency or 'ausente'} nao suportada; conversao nao permitida")
        affiliate_url = str(row.get("aw_deep_link") or "").strip()
        self._validate_affiliate_url(affiliate_url, row_number)
        source_url = str(row.get("merchant_deep_link") or "").strip() or affiliate_url
        self._validate_https_url(source_url, "merchant_deep_link", row_number)
        current = money_to_cents(row.get("search_price"))
        if not current:
            raise ValueError(f"linha {row_number}: search_price precisa ser positivo")
        # O feed chama rrp_price de preço de referência. Sem histórico verificado,
        # ele não prova um preço anterior nem autoriza alegar desconto.
        reference_price = money_to_cents(row.get("rrp_price"))
        product_id = str(row.get("aw_product_id") or "").strip()
        title = str(row.get("product_name") or "").strip()
        merchant_name = str(row.get("merchant_name") or "").strip()
        merchant_id = str(row.get("merchant_id") or "").strip()
        if not product_id or not title or not merchant_name or not merchant_id:
            raise ValueError(f"linha {row_number}: identificacao de produto/merchant ausente")
        image_urls = []
        for field in ("aw_image_url", "merchant_image_url", "large_image"):
            value = str(row.get(field) or "").strip()
            if value and self._is_https_url(value) and value not in image_urls:
                image_urls.append(value)
        explicit_stock = str(row.get("in_stock") or row.get("stock_status") or "").strip().lower()
        if explicit_stock in {"1", "true", "yes", "in stock", "in_stock", "available"}:
            stock_status = "IN_STOCK"
        elif explicit_stock in {"0", "false", "no", "out of stock", "out_of_stock", "unavailable"}:
            stock_status = "OUT_OF_STOCK"
        else:
            stock_status = "UNKNOWN"
        rating = self._optional_float(row.get("average_rating") or row.get("rating"))
        collected_at = str(row.get("last_updated") or utc_now()).strip()
        return Offer(
            merchant=merchant_name,
            affiliate_network="Awin",
            external_product_id=product_id,
            title=title,
            description=str(row.get("description") or row.get("product_short_description") or "").strip(),
            category=str(row.get("category_name") or row.get("merchant_category") or "").strip(),
            brand=str(row.get("brand_name") or "").strip(),
            original_price_cents=None,
            current_price_cents=current,
            discount_percent=None,
            rating=rating,
            image_urls=image_urls,
            source_url=source_url,
            affiliate_url=affiliate_url,
            deeplink=affiliate_url,
            collected_at=collected_at,
            expires_at=str(row.get("valid_to") or "").strip() or expires_after(collected_at),
            stock_status=stock_status,
            source_type="AWIN_OFFICIAL_PRODUCT_FEED",
            tracking_metadata={
                "merchant_id": merchant_id,
                "currency": "BRL",
                "commission_group": str(row.get("commission_group") or "").strip(),
                "rrp_reference_price_cents": reference_price,
            },
        )

    def _validate_affiliate_url(self, value: str, row_number: int) -> None:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        allowed = host == "awin1.com" or host.endswith(".awin1.com") or any(
            host == entry or host.endswith(f".{entry}") for entry in self.allowed_affiliate_hosts
        )
        if parsed.scheme != "https" or not allowed:
            raise ValueError(f"linha {row_number}: aw_deep_link nao usa host Awin/permitido")

    @staticmethod
    def _validate_https_url(value: str, field: str, row_number: int) -> None:
        if not AwinFeedAdapter._is_https_url(value):
            raise ValueError(f"linha {row_number}: {field} precisa ser URL HTTPS")

    @staticmethod
    def _is_https_url(value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", "."))
        except ValueError:
            return None
