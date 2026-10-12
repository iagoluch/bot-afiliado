"""Shopee Affiliate Open API: authenticated offer discovery, no automatic publishing.

The authenticated productOfferV2 result can include an account-specific offerLink.
It does NOT prove live stock; results are only candidates until stock validation.
No seller API, scraping, API secret output or unknown URL rewriting.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.parse import urlsplit

from app.config import Settings
from app.db import Database
from app.models import Offer, money_to_cents, utc_now
from app.services.collection import is_technology_offer

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_KEYWORDS = 5
MAX_LIMIT = 20
DEFAULT_KEYWORDS = ("ssd", "notebook", "celular", "headset")
_KEYWORD = re.compile(r"[\w \-/+,.]{2,80}\Z", re.UNICODE)


class AffiliateDiscoveryError(ValueError):
    """Safe public error: never contains API credentials or untrusted response."""


def _valid_url(value: Any, *, affiliate: bool) -> bool:
    if not isinstance(value, str) or len(value) > 2048 or not value:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            return False
        if affiliate:
            allowed = host in {"shope.ee", "shopee.com.br"} or host.endswith(".shopee.com.br")
        else:
            allowed = host == "shopee.com.br" or host.endswith(".shopee.com.br")
        return allowed and bool(parsed.path.strip("/"))
    except ValueError:
        return False


def _money(value: Any) -> int | None:
    raw = str(value or "")
    if not re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,2})?", raw):
        return None
    try:
        cents = money_to_cents(raw)
        return cents if cents is not None and cents > 0 else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _commission(value: Any) -> float | None:
    try:
        rate = Decimal(str(value))
        if not rate.is_finite() or not Decimal("0") < rate <= Decimal("1"):
            return None
        return float(rate * 100)
    except (ValueError, InvalidOperation):
        return None


def _nonnegative_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool) or not re.fullmatch(r"\d{1,16}", str(value)):
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _signed_request(settings: Settings, query: str, timestamp: int) -> urllib.request.Request:
    if not settings.shopee_app_id or not settings.shopee_secret:
        raise AffiliateDiscoveryError("BLOCKED_MISSING_SHOPEE_AFFILIATE_CREDENTIALS")
    app_id, secret = settings.shopee_app_id, settings.shopee_secret
    payload = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
    signature = hashlib.sha256(f"{app_id}{timestamp}{payload}{secret}".encode("utf-8")).hexdigest()
    return urllib.request.Request(
        API_URL,
        data=payload.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )


def _fetch_product_nodes(
    settings: Settings,
    keyword: str,
    limit: int,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timestamp: int | None = None,
) -> list[dict[str, Any]]:
    if not _KEYWORD.fullmatch(keyword):
        raise ValueError("keyword invalida para busca de afiliados")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit deve estar entre 1 e 20")
    # Fields supported by public Affiliate Open API documentation. Schema and
    # permissions MUST still be validated with approved Brazil credentials.
    query = (
        "query { productOfferV2(keyword: " + json.dumps(keyword, ensure_ascii=False)
        + f", sortType: 1, page: 1, limit: {limit}) "
        "{ nodes { itemId shopId productName productLink offerLink "
        "imageUrl priceMin priceMax commissionRate sales ratingStar periodEndTime } } }"
    )
    request = _signed_request(settings, query, int(time.time()) if timestamp is None else timestamp)
    try:
        with opener(request, timeout=15) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AffiliateDiscoveryError(f"SHOPEE_AFFILIATE_HTTP_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AffiliateDiscoveryError("SHOPEE_AFFILIATE_NETWORK_ERROR") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise AffiliateDiscoveryError("SHOPEE_AFFILIATE_RESPONSE_TOO_LARGE")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        raise AffiliateDiscoveryError("SHOPEE_AFFILIATE_INVALID_JSON") from None
    if not isinstance(data, dict):
        raise AffiliateDiscoveryError("SHOPEE_AFFILIATE_INVALID_SCHEMA")
    if data.get("errors"):
        errors = data["errors"]
        code = None
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            extension = errors[0].get("extensions")
            if isinstance(extension, dict):
                code = extension.get("code")
        # Do not return API's freeform error messages: they could contain URLs,
        # signed headers, credentials or other untrusted content.
        safe_code = str(code) if isinstance(code, int) or (
            isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,32}", code)
        ) else "SCHEMA_OR_ACCESS"
        raise AffiliateDiscoveryError(f"SHOPEE_AFFILIATE_API_ERROR_{safe_code}")
    result = data.get("data")
    offers = result.get("productOfferV2") if isinstance(result, dict) else None
    nodes = offers.get("nodes") if isinstance(offers, dict) else None
    if not isinstance(nodes, list):
        raise AffiliateDiscoveryError("SHOPEE_AFFILIATE_INVALID_SCHEMA")
    return [node for node in nodes[:limit] if isinstance(node, dict)]


def _normalize_node(node: dict[str, Any], *, now_ts: int) -> dict[str, Any] | None:
    item_id = _nonnegative_int(node.get("itemId"))
    shop_id = _nonnegative_int(node.get("shopId"))
    title = node.get("productName")
    source = node.get("productLink")
    affiliate_url = node.get("offerLink")
    if not (item_id and shop_id and isinstance(title, str) and 3 <= len(title.strip()) <= 250):
        return None
    if not _valid_url(source, affiliate=False) or not _valid_url(affiliate_url, affiliate=True):
        return None
    minimum, maximum = _money(node.get("priceMin")), _money(node.get("priceMax"))
    # A listing of multiple variants cannot honestly be advertised at a
    # single price; suppress it until a variant can be verified.
    if minimum is None or maximum is None or minimum != maximum:
        return None
    commission = _commission(node.get("commissionRate"))
    if commission is None:
        return None
    end = node.get("periodEndTime")
    if end not in (None, "", 0, "0"):
        end_ts = _nonnegative_int(end)
        if end_ts is None or end_ts <= now_ts:
            return None
    image = node.get("imageUrl")
    if not isinstance(image, str) or not image.startswith("https://"):
        image = None
    else:
        try:
            host = (urlsplit(image).hostname or "").lower()
            if host != "susercontent.com" and not host.endswith(".susercontent.com"):
                image = None
        except ValueError:
            image = None
    try:
        rating = float(str(node.get("ratingStar")))
        if not 0 <= rating <= 5:
            rating = None
    except (ValueError, TypeError):
        rating = None
    sales = _nonnegative_int(node.get("sales"))
    return {
        "external_product_id": f"{shop_id}_{item_id}",
        "title": title.strip(),
        "price_brl": f"{Decimal(minimum) / 100:.2f}",
        "source_url": source,
        "affiliate_url": affiliate_url,
        "image_url": image,
        "commission_percent": commission,
        "rating": rating,
        "sales": sales,
        "stock_status": "UNKNOWN",
        "stock_verified": False,
        "may_publish_to_telegram": False,
    }


def discover(
    settings: Settings,
    *,
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS,
    limit: int = 5,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timestamp: int | None = None,
) -> dict[str, Any]:
    if not keywords or len(keywords) > MAX_KEYWORDS:
        raise ValueError("informe de 1 a 5 keywords")
    if not settings.shopee_app_id or not settings.shopee_secret:
        return {
            "status": "BLOCKED_MISSING_SHOPEE_AFFILIATE_CREDENTIALS",
            "candidates": [],
            "may_publish_to_telegram": False,
        }
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    rejected = 0
    now_ts = int(time.time()) if timestamp is None else timestamp
    for keyword in keywords:
        for node in _fetch_product_nodes(
            settings, keyword, limit, opener=opener, timestamp=now_ts,
        ):
            candidate = _normalize_node(node, now_ts=now_ts)
            if candidate is None or not is_technology_offer(candidate, terms=settings.technology_offer_terms):
                rejected += 1
                continue
            identity = candidate["external_product_id"]
            if identity in seen:
                continue
            seen.add(identity)
            found.append(candidate)
    return {
        "status": "CANDIDATES_ONLY_STOCK_UNVERIFIED",
        "searched_keywords": list(keywords),
        "candidates": found,
        "rejected_or_unverifiable": rejected,
        "may_publish_to_telegram": False,
        "explanation": "Links retornados pela API afiliada; estoque nao verificado.",
    }


def stage_candidates(db: Database, report: dict[str, Any]) -> dict[str, Any]:
    """Persist without a content job. Cannot publish from staging."""
    if report.get("status") != "CANDIDATES_ONLY_STOCK_UNVERIFIED":
        raise ValueError("somente candidatos oficiais podem ser armazenados")
    created: list[int] = []
    for candidate in report.get("candidates", []):
        offer = Offer(
            merchant="Shopee",
            affiliate_network="Shopee Afiliados",
            external_product_id=candidate["external_product_id"],
            title=candidate["title"],
            category="",
            current_price_cents=money_to_cents(candidate["price_brl"]) or 0,
            source_url=candidate["source_url"],
            affiliate_url=candidate["affiliate_url"],
            deeplink=candidate["affiliate_url"],
            image_urls=[candidate["image_url"]] if candidate.get("image_url") else [],
            stock_status="UNKNOWN",
            rating=candidate["rating"],
            sales_count=candidate["sales"],
            commission_rate=candidate["commission_percent"],
            collected_at=utc_now(),
            source_type="SHOPEE_AFFILIATE_API_STOCK_UNVERIFIED",
            tracking_metadata={"discovery": "productOfferV2", "stock_verified": False},
        )
        created.append(db.upsert_offer(offer, content_campaign_id=None))
    return {
        "status": "STAGED_NO_PUBLICATION",
        "offer_ids": created,
        "stored": len(created),
        "queued": 0,
        "published": 0,
    }
