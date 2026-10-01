from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlsplit

from app.adapters.base import AffiliateAdapter, Capability, CapabilityStatus
from app.models import Offer, expires_after, money_to_cents, utc_now


MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ROWS = 50_000
MAX_FIELD_BYTES = 64_000
REQUIRED_FIELDS = frozenset({"name", "price", "currencyID", "url"})


class AdmitadFeedAdapter(AffiliateAdapter):
    """Importa um CSV exportado manualmente de um programa Admitad aprovado.

    O esquema e o link de um export brasileiro real ainda precisam de validação.
    Nenhuma oferta deste adapter deve ser publicada automaticamente só por importar.
    """

    name = "admitad_official_publisher_feed"
    integration_status = CapabilityStatus.MANUAL_OR_PENDING
    validation_status = "WAITING_FOR_REAL_EXPORT"
    capabilities = {
        Capability.DISCOVER_PRODUCTS: CapabilityStatus.MANUAL,
        Capability.DISCOVER_DEALS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_PRICE: CapabilityStatus.MANUAL,
        Capability.GET_COUPONS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_COMMISSION: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.CREATE_AFFILIATE_LINK: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_CONVERSIONS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.GET_REPORTS: CapabilityStatus.WAITING_FOR_CREDENTIALS,
        Capability.GET_CREATIVES: CapabilityStatus.MANUAL_OR_PENDING,
    }

    def __init__(
        self,
        merchant_name: str,
        *,
        delimiter: str = ",",
        columns: dict[str, str] | None = None,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_rows: int = MAX_ROWS,
    ) -> None:
        merchant_name = merchant_name.strip()
        if not merchant_name:
            raise ValueError("nome do programa Admitad e obrigatorio")
        if len(delimiter) != 1 or delimiter in {"\r", "\n", '"'}:
            raise ValueError("separador CSV invalido")
        if max_file_bytes < 1 or max_rows < 1:
            raise ValueError("limites do feed Admitad devem ser positivos")
        self.merchant_name = merchant_name
        self.delimiter = delimiter
        self.columns = {field: field for field in ("article", "vendorCode", "name", "price", "currencyID", "url", "picture", "categoryId", "description", "vendor")}
        if columns:
            unknown = set(columns) - set(self.columns)
            if unknown:
                raise ValueError(f"campos Admitad desconhecidos: {', '.join(sorted(unknown))}")
            self.columns.update(columns)
        if len(set(self.columns.values())) != len(self.columns):
            raise ValueError("colunas Admitad mapeadas em duplicidade")
        self.max_file_bytes = max_file_bytes
        self.max_rows = max_rows

    def import_offers(self, path: Path) -> list[Offer]:
        if path.suffix.lower() != ".csv":
            raise ValueError("export Admitad deve ser CSV local")
        if path.stat().st_size > self.max_file_bytes:
            raise ValueError(f"arquivo Admitad excede limite de {self.max_file_bytes} bytes")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=self.delimiter)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                raise ValueError("feed Admitad tem colunas duplicadas")
            missing = {self.columns[field] for field in REQUIRED_FIELDS} - set(headers)
            if missing:
                raise ValueError(f"feed Admitad sem colunas obrigatorias: {', '.join(sorted(missing))}")
            if self.columns["article"] not in headers and self.columns["vendorCode"] not in headers:
                raise ValueError("feed Admitad requer article ou vendorCode para identificar produto")
            offers: list[Offer] = []
            for row_number, row in enumerate(reader, start=2):
                if len(offers) >= self.max_rows:
                    raise ValueError(f"feed Admitad excede limite de {self.max_rows} linhas")
                if None in row or any(value is None or len(value.encode("utf-8")) > MAX_FIELD_BYTES for value in row.values()):
                    raise ValueError(f"linha {row_number}: estrutura ou campo CSV invalido")
                offers.append(self._normalize(row, row_number))
        if not offers:
            raise ValueError("feed Admitad nao contem produtos")
        return offers

    def _normalize(self, row: dict[str, str], row_number: int) -> Offer:
        def field(name: str) -> str:
            return (row.get(self.columns[name]) or "").strip()

        currency = field("currencyID").upper()
        if currency != "BRL":
            raise ValueError(f"linha {row_number}: moeda {currency or 'ausente'} nao suportada; conversao nao permitida")
        price = money_to_cents(field("price"))
        if not price:
            raise ValueError(f"linha {row_number}: price precisa ser positivo")
        product_id = field("article") or field("vendorCode")
        title = field("name")
        if not product_id or not title:
            raise ValueError(f"linha {row_number}: article/vendorCode e name sao obrigatorios")
        affiliate_url = field("url")
        self._validate_affiliate_url(affiliate_url, row_number)
        image_url = field("picture")
        if image_url and not self._is_https_url(image_url):
            raise ValueError(f"linha {row_number}: picture precisa ser URL HTTPS")
        collected_at = utc_now()
        return Offer(
            merchant=self.merchant_name,
            affiliate_network="Admitad",
            external_product_id=product_id,
            title=title,
            description=field("description"),
            category=field("categoryId"),
            brand=field("vendor"),
            current_price_cents=price,
            source_url=affiliate_url,
            affiliate_url=affiliate_url,
            deeplink=affiliate_url,
            image_urls=[image_url] if image_url else [],
            collected_at=collected_at,
            expires_at=expires_after(collected_at),
            source_type="ADMITAD_OFFICIAL_PUBLISHER_FEED_PENDING_VALIDATION",
            tracking_metadata={"currency": "BRL", "validation_status": self.validation_status},
        )

    @staticmethod
    def _is_https_url(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            return (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
                and not any(char.isspace() or ord(char) < 32 for char in value)
            )
        except ValueError:
            return False

    @classmethod
    def _validate_affiliate_url(cls, value: str, row_number: int) -> None:
        if not cls._is_https_url(value):
            raise ValueError(f"linha {row_number}: url precisa ser link afiliado HTTPS da Admitad")
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if host == "ad.admitad.com":
            valid_path = parsed.path.startswith("/g/") and len(parsed.path) > 3
        elif host == "fas.st":
            valid_path = parsed.path.startswith("/") and len(parsed.path) > 1
        else:
            valid_path = False
        if not valid_path:
            raise ValueError(f"linha {row_number}: url nao e link afiliado da Admitad")
