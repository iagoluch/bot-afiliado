from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from app.adapters.amazon_creators import (
    MARKETPLACE_BR,
    SEARCH_ITEMS_URL,
    SEARCH_RESOURCES,
    TOKEN_URL,
    AmazonCreatorsAdapter,
    AmazonCreatorsError,
    _RejectRedirects,
)
from app.adapters.awin_feed import AwinFeedAdapter
from app.adapters.base import Capability, CapabilityStatus
from app.adapters.mercadolivre_manual import MercadoLivreManualAdapter
from app.config import Settings
from app.db import Database
from app.services.media import CreativeGenerator
from app.services.p1 import P1Pipeline
from app.services.pipeline import Pipeline
from app.scheduler import run_due


ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit: int) -> bytes:
        return self.raw[:limit]


class RecordingOpener:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.requests: list[Request] = []

    def __call__(self, request: Request, *, timeout: int):
        assert timeout == 30
        self.requests.append(request)
        return Response(self.payloads.pop(0))


def amazon_response(*, tag: str = "exemplo-20", currency: str = "BRL") -> dict:
    data = json.loads((ROOT / "examples" / "amazon_creators_search.sample.json").read_text(encoding="utf-8"))
    item = data["searchResult"]["items"][0]
    item["detailPageURL"] = f"https://www.amazon.com.br/dp/B0EXAMPL0BR?tag={tag}&linkCode=osi"
    item["offersV2"]["listings"][0]["price"]["money"]["currency"] = currency
    return data


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "p2.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
        ffmpeg_path=str(tmp_path / "missing-ffmpeg"),
        amazon_partner_tag_br="exemplo-20",
    )


def test_amazon_creators_contract_and_safe_parser_preserve_official_link() -> None:
    opener = RecordingOpener([
        {"access_token": "token-value", "expires_in": 3600},
        amazon_response(),
    ])
    adapter = AmazonCreatorsAdapter("client", "secret", "exemplo-20", opener=opener)

    offer = adapter.search_items("fone bluetooth", item_count=3)[0]

    assert [request.full_url for request in opener.requests] == [TOKEN_URL, SEARCH_ITEMS_URL]
    token_body = json.loads(opener.requests[0].data)
    assert token_body == {
        "grant_type": "client_credentials",
        "client_id": "client",
        "client_secret": "secret",
        "scope": "creatorsapi::default",
    }
    search_request = opener.requests[1]
    search_body = json.loads(search_request.data)
    assert search_body == {
        "keywords": "fone bluetooth",
        "itemCount": 3,
        "marketplace": MARKETPLACE_BR,
        "partnerTag": "exemplo-20",
        "resources": list(SEARCH_RESOURCES),
        "searchIndex": "All",
    }
    assert search_request.get_header("Authorization") == "Bearer token-value"
    assert search_request.get_header("X-marketplace") == MARKETPLACE_BR
    assert search_request.get_header("User-agent") == "Agent/BotAfiliado"
    assert opener.requests[0].get_header("User-agent") == "Agent/BotAfiliado"
    assert offer.affiliate_url == "https://www.amazon.com.br/dp/B0EXAMPL0BR?tag=exemplo-20&linkCode=osi"
    assert offer.current_price_cents == 11990
    assert offer.original_price_cents is None
    assert offer.discount_percent is None
    assert adapter.capabilities[Capability.GET_PRICE] == CapabilityStatus.WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN


def test_amazon_short_token_is_not_cached_past_real_expiry() -> None:
    now = [100.0]
    opener = RecordingOpener([
        {"access_token": "short-1", "expires_in": 5},
        amazon_response(),
        amazon_response(),
        {"access_token": "short-2", "expires_in": 5},
        amazon_response(),
    ])
    adapter = AmazonCreatorsAdapter("client", "secret", "exemplo-20", opener=opener, clock=lambda: now[0])
    adapter.search_items("produto")
    now[0] = 104.0
    adapter.search_items("produto")
    now[0] = 104.6
    adapter.search_items("produto")
    assert [request.full_url for request in opener.requests].count(TOKEN_URL) == 2


def test_amazon_rejects_redirects_invalid_tag_currency_and_secret_leak() -> None:
    with pytest.raises(AmazonCreatorsError, match="redirect"):
        _RejectRedirects().redirect_request(None, None, 302, "Found", {}, "https://evil.example")
    with pytest.raises(ValueError, match="partnerTag"):
        AmazonCreatorsAdapter(None, None, "expected-20").parse_search_response(amazon_response(tag="other-20"))
    with pytest.raises(ValueError, match="conversao nao permitida"):
        AmazonCreatorsAdapter(None, None, "exemplo-20").parse_search_response(amazon_response(currency="USD"))

    def fail(request: Request, *, timeout: int):
        raise RuntimeError("secret-value")

    adapter = AmazonCreatorsAdapter("client", "secret-value", "exemplo-20", opener=fail)
    with pytest.raises(AmazonCreatorsError) as caught:
        adapter.search_items("produto")
    assert "secret-value" not in str(caught.value)


def test_amazon_saved_json_pipeline_is_fail_closed(tmp_path: Path) -> None:
    db = Database(tmp_path / "amazon.db")
    db.init()
    settings = settings_for(tmp_path)
    pipeline = Pipeline(db, settings, source_adapter="amazon-json")
    source = ROOT / "examples" / "amazon_creators_search.sample.json"
    with pytest.raises(ValueError, match="aprovacao escrita"):
        pipeline.ingest_source(source)
    assert db.rows("SELECT COUNT(*) AS n FROM offers")[0]["n"] == 0


def test_awin_csv_and_gzip_preserve_deep_link_and_prices(tmp_path: Path) -> None:
    source = ROOT / "examples" / "awin_feed.sample.csv"
    adapter = AwinFeedAdapter()
    csv_offer = adapter.import_offers(source)[0]
    gzip_path = tmp_path / "feed.csv.gz"
    with gzip.open(gzip_path, "wb") as handle:
        handle.write(source.read_bytes())
    gzip_offer = adapter.import_offers(gzip_path)[0]

    assert csv_offer.affiliate_url == gzip_offer.affiliate_url
    assert csv_offer.affiliate_url.startswith("https://www.awin1.com/")
    assert csv_offer.current_price_cents == 9990
    assert csv_offer.original_price_cents is None
    assert csv_offer.discount_percent is None
    assert csv_offer.tracking_metadata["rrp_reference_price_cents"] == 14990
    assert adapter.capabilities[Capability.GET_CONVERSIONS] == CapabilityStatus.WAITING_FOR_CREDENTIALS
    assert adapter.capabilities[Capability.CREATE_AFFILIATE_LINK] == CapabilityStatus.MANUAL_OR_PENDING


def test_awin_rejects_currency_unknown_host_and_uncompressed_limit(tmp_path: Path) -> None:
    source = (ROOT / "examples" / "awin_feed.sample.csv").read_text(encoding="utf-8")
    for name, modified, match in (
        ("usd.csv", source.replace(",BRL,", ",USD,"), "conversao nao permitida"),
        ("host.csv", source.replace("https://www.awin1.com/", "https://evil.example/"), "host Awin"),
    ):
        path = tmp_path / name
        path.write_text(modified, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            AwinFeedAdapter().import_offers(path)
    limited_gzip = tmp_path / "limited.csv.gz"
    with gzip.open(limited_gzip, "wb") as handle:
        handle.write(source.encode("utf-8"))
    with pytest.raises(ValueError, match="descompactado"):
        AwinFeedAdapter(max_uncompressed_bytes=20).import_offers(limited_gzip)


def test_mercado_livre_manual_preserves_official_link_and_capabilities(tmp_path: Path) -> None:
    adapter = MercadoLivreManualAdapter()
    offer = adapter.import_offers(ROOT / "examples" / "mercadolivre_offers.sample.csv")[0]
    assert adapter.integration_status == CapabilityStatus.MANUAL_OR_PENDING
    assert adapter.capabilities[Capability.CREATE_AFFILIATE_LINK] == CapabilityStatus.MANUAL
    assert offer.affiliate_url == "https://mercadolivre.com/sec/Exemplo?label=canal-publico"
    assert offer.original_price_cents is None
    assert offer.discount_percent is None
    assert offer.tracking_metadata == {
        "official_link_preserved": True,
        "unverified_reference_price_cents": 19990,
    }

    source = (ROOT / "examples" / "mercadolivre_offers.sample.csv").read_text(encoding="utf-8")
    invalid = tmp_path / "invalid-ml.csv"
    invalid.write_text(source.replace("https://mercadolivre.com/sec/Exemplo?label=canal-publico", "https://example.com/link"), encoding="utf-8")
    with pytest.raises(ValueError, match="oficial do Mercado Livre"):
        adapter.import_offers(invalid)


def test_p2_dry_run_and_multichannel_package_are_idempotent_without_external_publish(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    pipeline = Pipeline(db, settings, source_adapter="mercadolivre")

    source = tmp_path / "mercadolivre.csv"
    source.write_text(
        (ROOT / "examples" / "mercadolivre_offers.sample.csv")
        .read_text(encoding="utf-8")
        .replace(",10,https://", ",20,https://"),
        encoding="utf-8",
    )
    result = pipeline.run(source, "p2-e2e")
    assert [publication["status"] for publication in result["publications"]] == ["SIMULATED"]
    assert db.overview()["publications"] == 0
    offer_id = result["offers"][0]

    generator = CreativeGenerator(
        settings.creatives_path,
        ffmpeg_path=settings.ffmpeg_path,
        image_loader=lambda _: None,
    )
    p1 = P1Pipeline(db, settings, generator)
    first = p1.generate_offer(offer_id, "p2-e2e")
    second = p1.generate_offer(offer_id, "p2-e2e")
    assert len(first["packages"]) == 6
    assert first["packages"] == second["packages"]
    assert {package["channel"] for package in first["packages"]} == {"instagram", "tiktok", "site", "telegram"}
    assert db.rows("SELECT COUNT(*) AS n FROM content_packages")[0]["n"] == 7
    telegram = next(package for package in first["packages"] if package["channel"] == "telegram")
    row = db.rows("SELECT body FROM content_packages WHERE id=?", (telegram["content_id"],))[0]
    assert f"/go/{offer_id}?" in row["body"]
    assert db.overview()["publications"] == 0


def test_scheduler_claims_same_slot_independently_per_adapter(tmp_path: Path) -> None:
    db = Database(tmp_path / "scheduler.db")
    db.init()

    class FakePipeline:
        settings = SimpleNamespace(dry_run=True)

        def __init__(self, source_adapter: str):
            self.source_adapter = source_adapter

        def run(self, source: Path, campaign_id: str):
            return {"adapter": self.source_adapter, "campaign_id": campaign_id}

    now = datetime(2026, 9, 30, 10, 2)
    shopee = run_due(db, FakePipeline("shopee"), tmp_path / "one.csv", now)
    awin = run_due(db, FakePipeline("awin"), tmp_path / "two.csv", now)
    assert shopee and shopee["adapter"] == "shopee"
    assert awin and awin["adapter"] == "awin"
