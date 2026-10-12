from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path

import pytest

from app.mercadolivre_catalog import CatalogProbeError
from app.mercadolivre_candidates import candidates


def _tokens(tmp_path):
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps({"access_token": "private", "expires_at": time.time()+3600}))
    return p


def test_candidates_when_buybox_absent(tmp_path):
    visited = []
    def opener(req, timeout):
        visited.append((req.full_url, req.get_method(), req.get_header("Authorization")))
        return io.BytesIO(json.dumps({"results": [
            {"item_id": "MLB43210001", "price": 250.00, "currency_id": "BRL", "available_quantity": 3, "seller_id": 9},
            {"item_id": "MLB43210002", "price": 199.90, "currency_id": "BRL", "available_quantity": 2, "seller_id": 5},
            {"item_id": "MLB43210003", "price": 9.90, "currency_id": "BRL", "available_quantity": 0},
            {"item_id": "MLB43210004", "price": 4.00, "currency_id": "USD", "available_quantity": 2},
        ]}).encode())
    result = candidates("MLB27880616", token_path=_tokens(tmp_path), opener=opener)
    assert visited == [("https://api.mercadolibre.com/products/MLB27880616/items", "GET", "Bearer private")]
    assert result["status"] == "CANDIDATES_FOUND"
    assert [x["item_id"] for x in result["results"]] == ["MLB43210002", "MLB43210001"]
    assert result["has_affiliate_links"] is False
    assert result["may_publish_to_telegram"] is False
    assert "private" not in str(result)
    assert all("affiliate_url" in x and x["affiliate_url"] is None for x in result["results"])


def test_no_results_does_not_invent_links(tmp_path):
    result=candidates("MLB27880616",token_path=_tokens(tmp_path),opener=lambda req,timeout: io.BytesIO(b'{"results":[]}'))
    assert result["status"]=="NO_SELLABLE_CANDIDATES"


@pytest.mark.parametrize("value", ["", "MLB", "MLA123456", "../bad", "MLB11", "MLB1?"])
def test_bad_catalog_id_no_http(tmp_path,value):
    with pytest.raises(ValueError):
        candidates(value,token_path=_tokens(tmp_path),opener=lambda req,timeout: pytest.fail("network"))


def test_private_resource_403_fail_closed(tmp_path):
    def opener(req,timeout):
        raise urllib.error.HTTPError(req.full_url,403,"forbidden",{},io.BytesIO(b'{"secret":"private"}'))
    with pytest.raises(CatalogProbeError,match="HTTP 403") as err:
        candidates("MLB27880616",token_path=_tokens(tmp_path),opener=opener)
    assert "private" not in str(err.value)


@pytest.mark.parametrize("bad", [b'[]',b'{}',b'{"results":{}}'])
def test_invalid_response_shape(tmp_path,bad):
    with pytest.raises(CatalogProbeError):
        candidates("MLB27880616",token_path=_tokens(tmp_path),opener=lambda req,timeout:io.BytesIO(bad))


def test_limit_bounds_and_unverified_prices(tmp_path):
    for n in (0,11):
        with pytest.raises(ValueError):
            candidates("MLB27880616",limit=n,token_path=_tokens(tmp_path),opener=lambda req,timeout:pytest.fail("network"))
    response={"results":[{"item_id":"MLB43210001","price":"1e10000","currency_id":"BRL","available_quantity":2}]}
    result=candidates("MLB27880616",token_path=_tokens(tmp_path),opener=lambda req,timeout:io.BytesIO(json.dumps(response).encode()))
    assert result["status"]=="NO_SELLABLE_CANDIDATES"
