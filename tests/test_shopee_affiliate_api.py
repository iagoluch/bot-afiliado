from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from dataclasses import replace

import pytest

from app.config import DEFAULT_TECH_OFFER_TERMS, Settings
from app.db import Database
from app.services.pipeline import Pipeline
from app.shopee_affiliate_api import (
    AffiliateDiscoveryError, _signed_request, discover, stage_candidates,
)


def _settings(tmp_path, *, app_id="12345", secret="test-only-secret"):
    return Settings(
        database_path=tmp_path / "affiliate.db",
        dry_run=False,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token="fake",
        telegram_chat_id="@test",
        telegram_only_mode=True,
        telegram_real_enabled=True,
        channel_visibility={"telegram": "public"},
        shopee_app_id=app_id,
        shopee_secret=secret,
        technology_offer_terms=DEFAULT_TECH_OFFER_TERMS,
    )


def _node(**changes):
    node = {
        "itemId": "9990001",
        "shopId": "800002",
        "productName": "SSD NVMe 1TB computador",
        "productLink": "https://shopee.com.br/product/800002/9990001",
        "offerLink": "https://s.shopee.com.br/EXAMPLEOFFICIAL",
        "priceMin": "299.90",
        "priceMax": "299.90",
        "commissionRate": "0.15",
        "sales": "900",
        "ratingStar": "4.8",
        "imageUrl": "https://down-br.img.susercontent.com/file/valid.jpg",
        "periodEndTime": "0",
    }
    node.update(changes)
    return node


def _opener(nodes, *, requests=None):
    response = json.dumps({"data": {"productOfferV2": {"nodes": nodes}}}).encode()
    def open_(req, timeout):
        if requests is not None:
            requests.append((req, timeout))
        return io.BytesIO(response)
    return open_


def test_signs_exact_payload_and_never_includes_secret_in_request_body(tmp_path):
    settings = _settings(tmp_path)
    req = _signed_request(settings, "query { productOfferV2 { nodes { itemId } } }", 1700000000)
    body = req.data.decode()
    signature = hashlib.sha256(("12345" + "1700000000" + body + "test-only-secret").encode()).hexdigest()
    assert req.get_method() == "POST"
    assert req.full_url == "https://open-api.affiliate.shopee.com.br/graphql"
    assert req.get_header("Authorization") == (
        "SHA256 Credential=12345, Timestamp=1700000000, Signature=" + signature
    )
    assert "test-only-secret" not in body
    assert "EXAMPLEOFFICIAL" not in body


def test_discovers_authenticated_affiliate_links_and_filters_niche(tmp_path):
    settings = _settings(tmp_path)
    requests=[]
    report = discover(settings, keywords=("ssd",), limit=5, timestamp=1700000000, opener=_opener([
        _node(),
        _node(itemId="9990002", productName="Kit jogo de panelas para cozinha"),
        _node(itemId="9990003", productName="Monitor gamer 27", offerLink="https://attacker.test/steal"),
        _node(itemId="9990004", productName="Mouse USB-C", priceMin="40.00", priceMax="60.00"),
        _node(itemId="9990005", productName="Notebook i5", commissionRate="0"),
        _node(),  # capped by --limit
    ], requests=requests))
    assert report["status"] == "CANDIDATES_ONLY_STOCK_UNVERIFIED"
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["affiliate_url"] == "https://s.shopee.com.br/EXAMPLEOFFICIAL"
    assert candidate["price_brl"] == "299.90"
    assert candidate["commission_percent"] == 15
    assert candidate["stock_status"] == "UNKNOWN"
    assert candidate["may_publish_to_telegram"] is False
    assert report["rejected_or_unverifiable"] == 4
    assert "test-only-secret" not in json.dumps(report)
    req,timeout=requests[0]
    assert timeout == 15
    assert 'keyword: "ssd"' in json.loads(req.data)["query"]


def test_deduplicates_across_keywords(tmp_path):
    result=discover(_settings(tmp_path),keywords=("ssd","notebook"),limit=5,opener=_opener([_node()]))
    assert len(result["candidates"])==1


def test_missing_credentials_zero_network(tmp_path):
    settings=_settings(tmp_path,app_id=None,secret=None)
    report=discover(settings,opener=lambda *a,**k: pytest.fail("network called"))
    assert report["status"]=="BLOCKED_MISSING_SHOPEE_AFFILIATE_CREDENTIALS"
    assert not report["candidates"]


def test_api_errors_fail_closed_without_echoing_secret(tmp_path):
    def denied(req, timeout):
        return io.BytesIO(b'{"errors":[{"message":"secret test-only-secret", "extensions":{"code":10035}}],"data":{}}')
    with pytest.raises(AffiliateDiscoveryError,match="API_ERROR_10035") as e:
        discover(_settings(tmp_path),keywords=("ssd",),opener=denied)
    assert "test-only-secret" not in str(e.value)


def test_http_error_does_not_leak_body(tmp_path):
    def denied(req, timeout):
        raise urllib.error.HTTPError(req.full_url,403,"Test forbidden",{},io.BytesIO(b"SECRET"))
    with pytest.raises(AffiliateDiscoveryError,match="HTTP_403") as e:
        discover(_settings(tmp_path),keywords=("ssd",),opener=denied)
    assert "SECRET" not in str(e.value)


@pytest.mark.parametrize("payload", [b"[]",b'{"data":{}}',b'{"data":{"productOfferV2":{}}}', b"not-json"])
def test_invalid_schema_or_json_fail_closed(tmp_path,payload):
    with pytest.raises(AffiliateDiscoveryError):
        discover(_settings(tmp_path),keywords=("ssd",),opener=lambda req,timeout:io.BytesIO(payload))


@pytest.mark.parametrize("invalid", [
    {"offerLink":"http://s.shopee.com.br/link"},
    {"offerLink":"https://s.shopee.com.br.attacker.test/link"},
    {"offerLink":"https://shopee.com.br@evil.test/item"},
    {"productLink":"https://localhost/item"},
    {"productName":"Camiseta casual feminina"},
    {"priceMin":"1e10000","priceMax":"1e10000"},
    {"commissionRate":"nan"},
    {"commissionRate":"-0.2"},
    {"itemId":"../inject"},
    {"shopId":""},
])
def test_invalid_or_unrelated_offers_not_accepted(tmp_path,invalid):
    result=discover(_settings(tmp_path),keywords=("ssd",),opener=_opener([_node(**invalid)]))
    assert result["candidates"] == []


def test_stage_never_creates_telegram_job_even_in_real_mode(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    report=discover(settings,keywords=("ssd",),opener=_opener([_node()]))
    stage=stage_candidates(db,report)
    assert stage["status"]=="STAGED_NO_PUBLICATION"
    assert stage["stored"]==1 and stage["queued"]==0 and stage["published"]==0
    assert db.rows("SELECT COUNT(*) n FROM content_jobs")[0]["n"]==0
    assert db.rows("SELECT COUNT(*) n FROM publish_queue")[0]["n"]==0
    oid=stage["offer_ids"][0]
    offer=db.get_offer(oid)
    assert offer["stock_status"]=="UNKNOWN"
    assert offer["source_type"]=="SHOPEE_AFFILIATE_API_STOCK_UNVERIFIED"
    pipeline=Pipeline(db,settings)
    assert pipeline.curate_and_queue([oid],"no-publish") == []
    assert pipeline.process_one() is None
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0


def test_invalid_keyword_fails_before_http(tmp_path):
    with pytest.raises(ValueError):
        discover(_settings(tmp_path),keywords=('ssd") { exploit',),opener=lambda *a,**k:pytest.fail("network"))


def test_staging_rejects_invented_report(tmp_path):
    db=Database(_settings(tmp_path).database_path)
    db.init()
    with pytest.raises(ValueError):
        stage_candidates(db,{"status":"FAKE","candidates":[_node()]})


def test_expired_campaign_is_not_staged(tmp_path):
    report=discover(_settings(tmp_path),keywords=("ssd",),timestamp=1700000000,
        opener=_opener([_node(periodEndTime=1690000000)]))
    assert not report["candidates"]
