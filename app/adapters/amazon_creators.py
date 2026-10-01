from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.adapters.base import AffiliateAdapter, Capability, CapabilityStatus
from app.models import Offer, money_to_cents, utc_now


TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SEARCH_ITEMS_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
MARKETPLACE_BR = "www.amazon.com.br"
MAX_RESPONSE_BYTES = 40 * 1024
AGENT_USER_AGENT = "Agent/BotAfiliado"
SEARCH_RESOURCES = (
    "browseNodeInfo.browseNodes",
    "images.primary.large",
    "itemInfo.byLineInfo",
    "itemInfo.features",
    "itemInfo.title",
    "offersV2.listings.availability",
    "offersV2.listings.merchantInfo",
    "offersV2.listings.price",
)


class AmazonCreatorsError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise AmazonCreatorsError("redirect HTTP recusado pela integracao Amazon")


def _open_without_redirects(request: Request, *, timeout: int):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


class AmazonCreatorsAdapter(AffiliateAdapter):
    name = "amazon_creators_api_br"
    integration_status = CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN
    capabilities = {
        Capability.DISCOVER_PRODUCTS: CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN,
        Capability.DISCOVER_DEALS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_PRICE: CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN,
        Capability.GET_COUPONS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_COMMISSION: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.CREATE_AFFILIATE_LINK: CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN,
        Capability.GET_CONVERSIONS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_REPORTS: CapabilityStatus.MANUAL_OR_PENDING,
        Capability.GET_CREATIVES: CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN,
    }

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        partner_tag: str | None,
        *,
        opener: Callable[..., Any] = _open_without_redirects,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.partner_tag = partner_tag
        self.opener = opener
        self._live_network_blocked = opener is _open_without_redirects
        self.clock = clock
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _require_partner_tag(self) -> str:
        if not self.partner_tag:
            raise AmazonCreatorsError("AMAZON_PARTNER_TAG_BR ausente")
        return self.partner_tag

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception as exc:
            # Credenciais e corpo de resposta não entram na mensagem persistível.
            raise AmazonCreatorsError(f"falha HTTP Amazon Creators API: {type(exc).__name__}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AmazonCreatorsError("resposta Amazon excede limite conservador de 40 KB")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AmazonCreatorsError("resposta Amazon nao e JSON valido") from exc
        if not isinstance(data, dict):
            raise AmazonCreatorsError("resposta Amazon precisa ser objeto JSON")
        return data

    def _token(self) -> str:
        if self._access_token and self.clock() < self._token_expires_at:
            return self._access_token
        if not self.client_id or not self.client_secret:
            raise AmazonCreatorsError("credenciais AMAZON_CREATORS_CLIENT_ID/SECRET ausentes")
        data = self._post_json(
            TOKEN_URL,
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "creatorsapi::default",
            },
            {"Content-Type": "application/json", "User-Agent": AGENT_USER_AGENT},
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise AmazonCreatorsError("resposta OAuth Amazon sem access_token")
        try:
            expires_in = int(data.get("expires_in", 3600))
        except (TypeError, ValueError) as exc:
            raise AmazonCreatorsError("expires_in OAuth Amazon invalido") from exc
        if expires_in <= 0:
            raise AmazonCreatorsError("expires_in OAuth Amazon precisa ser positivo")
        safety_margin = min(30.0, expires_in / 10)
        self._access_token = token
        self._token_expires_at = self.clock() + expires_in - safety_margin
        return token

    def search_items(self, keywords: str, *, item_count: int = 10) -> list[Offer]:
        if self._live_network_blocked:
            raise AmazonCreatorsError("Amazon live bloqueada: aguarda aprovacao escrita e desenho de retencao")
        keywords = keywords.strip()
        if not keywords:
            raise ValueError("keywords Amazon nao pode ser vazio")
        if not 1 <= item_count <= 10:
            raise ValueError("item_count Amazon deve estar entre 1 e 10")
        partner_tag = self._require_partner_tag()
        data = self._post_json(
            SEARCH_ITEMS_URL,
            {
                "keywords": keywords,
                "itemCount": item_count,
                "marketplace": MARKETPLACE_BR,
                "partnerTag": partner_tag,
                "resources": list(SEARCH_RESOURCES),
                "searchIndex": "All",
            },
            {
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "User-Agent": AGENT_USER_AGENT,
                "x-marketplace": MARKETPLACE_BR,
            },
        )
        return self.parse_search_response(data)

    def import_offers(self, path: Path) -> list[Offer]:
        if path.stat().st_size > MAX_RESPONSE_BYTES:
            raise ValueError("JSON Amazon excede limite conservador de 40 KB")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("arquivo Amazon nao e JSON valido") from exc
        if not isinstance(data, dict):
            raise ValueError("arquivo Amazon precisa conter objeto JSON")
        return self.parse_search_response(data)

    def parse_search_response(self, data: dict[str, Any]) -> list[Offer]:
        if data.get("errors"):
            raise ValueError("resposta SearchItems contem erros")
        result = data.get("searchResult")
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ValueError("resposta SearchItems sem searchResult.items")
        offers = [self._normalize_item(item) for item in items]
        if not offers:
            raise ValueError("SearchItems nao retornou ofertas utilizaveis")
        return offers

    def _normalize_item(self, item: Any) -> Offer:
        if not isinstance(item, dict):
            raise ValueError("item Amazon invalido")
        asin = str(item.get("asin") or "").strip()
        title = str(((item.get("itemInfo") or {}).get("title") or {}).get("displayValue") or "").strip()
        detail_url = str(item.get("detailPageURL") or "").strip()
        if not asin or not title:
            raise ValueError("item Amazon sem ASIN ou titulo")
        self._validate_affiliate_url(detail_url)
        listings = ((item.get("offersV2") or {}).get("listings") or [])
        if not isinstance(listings, list) or not listings:
            raise ValueError(f"item Amazon {asin} sem OffersV2 listing")
        listing = next((entry for entry in listings if isinstance(entry, dict) and entry.get("isBuyBoxWinner") is True), listings[0])
        if not isinstance(listing, dict):
            raise ValueError(f"item Amazon {asin} com listing invalido")
        money = (((listing.get("price") or {}).get("money")) or {})
        currency = str(money.get("currency") or "").upper()
        if currency != "BRL":
            raise ValueError(f"item Amazon {asin} em moeda {currency or 'ausente'}; conversao nao permitida")
        current_price = money_to_cents(money.get("amount"))
        if not current_price:
            raise ValueError(f"item Amazon {asin} sem preco positivo")

        features = (((item.get("itemInfo") or {}).get("features") or {}).get("displayValues") or [])
        description = " | ".join(str(value).strip() for value in features[:3] if str(value).strip())
        byline = ((item.get("itemInfo") or {}).get("byLineInfo") or {})
        brand = str(((byline.get("brand") or {}).get("displayValue")) or "").strip()
        nodes = ((item.get("browseNodeInfo") or {}).get("browseNodes") or [])
        category = str(nodes[0].get("displayName") or "").strip() if nodes and isinstance(nodes[0], dict) else ""
        availability = listing.get("availability") or {}
        availability_type = str(availability.get("type") or "").upper()
        stock_status = "IN_STOCK" if availability_type == "IN_STOCK" else ("OUT_OF_STOCK" if "OUT" in availability_type else "UNKNOWN")
        merchant = str(((listing.get("merchantInfo") or {}).get("name")) or "Amazon").strip()
        return Offer(
            merchant=merchant or "Amazon",
            affiliate_network="Amazon Associados",
            external_product_id=asin,
            title=title,
            description=description,
            category=category,
            brand=brand,
            current_price_cents=current_price,
            image_urls=[],
            source_url=detail_url,
            affiliate_url=detail_url,
            deeplink=detail_url,
            collected_at=utc_now(),
            stock_status=stock_status,
            source_type="AMAZON_CREATORS_API",
            tracking_metadata={"partner_tag": self._require_partner_tag(), "marketplace": MARKETPLACE_BR, "currency": "BRL"},
        )

    def _validate_affiliate_url(self, value: str) -> None:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "amazon.com.br" or host.endswith(".amazon.com.br")):
            raise ValueError("detailPageURL precisa ser HTTPS oficial Amazon Brasil")
        if self._require_partner_tag() not in parse_qs(parsed.query).get("tag", []):
            raise ValueError("detailPageURL Amazon nao contem o partnerTag configurado")
