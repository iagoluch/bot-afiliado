from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.mercadolivre_catalog import CatalogProbeError
from app.mercadolivre_listing import inspect_listing, _price_cents

PRODUCT = "MLB27880616"
ITEM = "MLB1234567890"


def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "ml_tokens.json"
    path.write_text(json.dumps({
        "access_token": "not-a-real-token",
        "expires_at": time.time() + 3600,
    }))
    return path


def json_reply(obj: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(obj).encode("utf-8"))


def product(winner=True):
    return {
        "id": PRODUCT, "name": "SSD NVMe Hikvision E3000 1TB", "status": "active",
        "buy_box_winner": {"item_id": ITEM} if winner else None,
    }


def item(**changes):
    data = {
        "id": ITEM,
        "site_id": "MLB",
        "catalog_product_id": PRODUCT,
        "title": "SSD NVMe Hikvision 1TB original",
        "status": "active",
        "price": 299.90,
        "currency_id": "BRL",
        "available_quantity": 4,
        "seller_id": 123,
        "permalink": "https://www.mercadolivre.com.br/p/MLB1234567890",
        "pictures": [
            {"secure_url": "https://evil.test/x.png"},
            {"secure_url": "https://http2.mlstatic.com/product.jpg"},
        ],
        "affiliate_url": "https://not-valid-affiliate.test",
    }
    data.update(changes)
    return data


def test_inspect_winner_item_is_read_only_and_never_affiliate(tmp_path):
    paths = []

    def opener(req, timeout):
        paths.append((req.full_url, req.get_method(), timeout, req.get_header("Authorization")))
        return json_reply(product() if len(paths) == 1 else item())

    result = inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)
    assert [urlsplit(x[0]).path for x in paths] == [
        f"/products/{PRODUCT}", f"/items/{ITEM}",
    ]
    assert all(x[1:] == ("GET", 15, "Bearer not-a-real-token") for x in paths)
    assert result["status"] == "LISTING_VERIFIED"
    assert result["price_brl"] == "299.90"
    assert result["image_url"] == "https://http2.mlstatic.com/product.jpg"
    assert result["product_url"] == "https://www.mercadolivre.com.br/p/MLB1234567890"
    assert result["has_sellable_listing"] is True
    assert result["has_affiliate_link"] is False
    assert result["may_publish_to_telegram"] is False
    assert "affiliate_url" not in result
    assert "not-a-real-token" not in str(result)


def test_catalog_without_winner_does_not_request_listing(tmp_path):
    calls = []
    def opener(req, timeout):
        calls.append(req.full_url)
        return json_reply(product(winner=False))
    result = inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)
    assert result["status"] == "NO_ACTIVE_LISTING"
    assert len(calls) == 1
    assert result["has_sellable_listing"] is False


@pytest.mark.parametrize("changes", [
    {"status": "paused"},
    {"price": None},
    {"price": 0},
    {"price": "NaN"},
    {"price": 129.999},
    {"available_quantity": 0},
    {"currency_id": "USD"},
    {"permalink": "http://www.mercadolivre.com.br/produto"},
    {"permalink": "https://mercadolivre.com.br.evil.test/product"},
])
def test_listing_without_complete_verified_purchase_data_is_rejected(tmp_path, changes):
    def opener(req, timeout):
        return json_reply(product() if "/products/" in req.full_url else item(**changes))
    result = inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)
    assert result["status"] == "NO_ACTIVE_LISTING"
    assert "price_brl" not in result


@pytest.mark.parametrize("bad_id", ["MLA123456", "../users/me", "MLB", "MLB1?x", "https://evil.test"])
def test_untrusted_ids_are_rejected_without_network(tmp_path, bad_id):
    with pytest.raises(ValueError, match="ID de catalogo"):
        inspect_listing(bad_id, token_path=token_file(tmp_path), opener=lambda *_a, **_k: pytest.fail("network"))


@pytest.mark.parametrize("code", [401, 403, 404, 429, 500])
def test_api_errors_are_safe_and_have_no_token_in_exception(tmp_path, code):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "blocked", {}, io.BytesIO(b'{"secret":"should-not-show"}'))
    with pytest.raises(CatalogProbeError, match=f"HTTP {code}") as exc:
        inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)
    assert "should-not-show" not in str(exc.value)
    assert "not-a-real-token" not in str(exc.value)


def test_wrong_item_catalog_is_rejected(tmp_path):
    def opener(req, timeout):
        return json_reply(product() if "/products/" in req.full_url else item(catalog_product_id="MLB55555555"))
    with pytest.raises(CatalogProbeError, match="nao pertence"):
        inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)


def test_mismatched_catalog_is_rejected(tmp_path):
    def opener(req, timeout):
        return json_reply({"id": "MLB9999999", "status": "active"})
    with pytest.raises(CatalogProbeError, match="divergente"):
        inspect_listing(PRODUCT, token_path=token_file(tmp_path), opener=opener)


def test_invalid_prices_fail_closed():
    assert _price_cents(True) is None
    assert _price_cents(float("nan")) is None
    assert _price_cents(float("inf")) is None
    assert _price_cents("100.01") == 10001
    assert _price_cents("1e10000") is None
