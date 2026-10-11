from __future__ import annotations

import io
import json
from dataclasses import replace
from urllib.error import HTTPError, URLError

import pytest

from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.services.content import telegram_direct_message, telegram_product_photo
from app.services.pipeline import Pipeline
from app.services.telegram import TelegramClient, TelegramDeliveryUncertain
from app.worker import _source_from_env
from app.services.collection import prepare_assisted_offers


def _settings(tmp_path, **changes):
    obj = Settings(
        database_path=tmp_path / "bot.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_only_mode=True,
        creatives_path=tmp_path / "creatives",
    )
    return replace(obj, **changes)


def _item():
    return {
        "external_product_id": "tech-123",
        "title": "SSD NVMe 1 TB",
        "current_price": "299.90",
        "category": "informatica",
        "source_url": "https://shopee.com.br/product/1/tech-123",
        "affiliate_url": "https://s.shopee.com.br/tech-123?sub_id=camp1",
        "collected_at": utc_now(),
        "stock_status": "IN_STOCK",
        "rating": "4.9",
        "sales_count": "5000",
        "commission_rate": "20",
        "coupon": "TECH10",
        "shipping": "frete gratis",
        "image_urls": ["https://down-br.img.susercontent.com/abc123.jpg"],
    }


def test_direct_copy_uses_original_affiliate_link_without_unverified_discount(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    db.init()
    batch = prepare_assisted_offers("shopee", [_item()], terms=("ssd",))
    offer_id = db.upsert_offer(batch.offers[0])
    offer = dict(db.get_offer(offer_id))
    body = telegram_direct_message(offer)
    assert body.startswith("#publi")
    assert "R$ 299,90" in body
    assert "https://s.shopee.com.br/tech-123?sub_id=camp1" in body
    assert "/go/" not in body
    assert "BAIXOU" not in body
    assert "De R$" not in body


def test_photo_selection_uses_official_domains_only():
    assert telegram_product_photo({"image_urls_json": json.dumps([
        "http://127.0.0.1/private",
        "https://evil.example/photo.jpg",
        "https://http2.mlstatic.com/genuine.jpg",
    ])}) == "https://http2.mlstatic.com/genuine.jpg"
    assert telegram_product_photo({"image_urls": ["https://susercontent.com.evil.test/x"]}) is None


def test_local_url_accepted_only_in_explicit_telegram_only_live_mode(tmp_path, monkeypatch):
    for key in ("DRY_RUN", "PUBLIC_BASE_URL", "TELEGRAM_ONLY_MODE", "TELEGRAM_REAL_ENABLED",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CHANNEL_VISIBILITY_JSON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("TELEGRAM_ONLY_MODE", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-test-only")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@publicchannel")
    with pytest.raises(ValueError, match="TELEGRAM_REAL_ENABLED"):
        Settings.from_env(env_file=tmp_path / ".missing")
    monkeypatch.setenv("TELEGRAM_REAL_ENABLED", "true")
    settings = Settings.from_env(env_file=tmp_path / ".missing")
    assert not settings.dry_run
    assert settings.telegram_only_mode
    assert settings.telegram_real_enabled


def test_worker_source_real_requires_both_telegram_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_SOURCE_MODE", "database")
    settings = _settings(tmp_path, dry_run=False, telegram_real_enabled=False)
    with pytest.raises(ValueError, match="TELEGRAM_REAL_ENABLED"):
        _source_from_env(settings)
    assert _source_from_env(replace(settings, telegram_real_enabled=True)) is None


def test_photo_http400_definite_rejection_falls_back_to_one_text_message():
    requests = []
    def opener(req, timeout):
        requests.append(req.full_url)
        if req.full_url.endswith("/sendPhoto"):
            raise HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"ok":false,"error_code":400}'))
        return io.BytesIO(b'{"ok":true,"result":{"message_id":37}}')
    client = TelegramClient("dummy", "@publicchannel", dry_run=False, opener=opener)
    result = client.send_photo("#publi\nOferta", "https://http2.mlstatic.com/product.jpg")
    assert result.message_id == "37"
    assert len(requests) == 2
    assert requests[0].endswith("/sendPhoto")
    assert requests[1].endswith("/sendMessage")


def test_photo_transport_uncertain_is_not_resent_as_text():
    requests = []
    def opener(req, timeout):
        requests.append(req.full_url)
        raise URLError("timeout")
    client = TelegramClient("dummy", "@publicchannel", dry_run=False, opener=opener)
    with pytest.raises(TelegramDeliveryUncertain):
        client.send_photo("#publi\nOferta", "https://http2.mlstatic.com/product.jpg")
    assert len(requests) == 1


def test_telegram_only_queue_simulates_without_network(tmp_path):
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    db.init()
    batch = prepare_assisted_offers("shopee", [_item()], terms=("ssd",))
    telegram = TelegramClient(
        None, None, dry_run=True,
        opener=lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("network")),
    )
    pipeline = Pipeline(db, settings, telegram=telegram, source_adapter="assisted")
    ids = pipeline.ingest_offers(batch.offers, content_campaign_id="test-telegram")
    assert ids
    queued = pipeline.curate_and_queue(ids, "test-telegram")
    assert len(queued) == 1
    sent = pipeline.process_one()
    assert sent["status"] == "SIMULATED"
    assert db.rows("SELECT COUNT(*) AS n FROM publications WHERE dry_run=0")[0]["n"] == 0
    body = db.rows("SELECT body FROM content_packages WHERE channel='telegram'")[0]["body"]
    assert "/go/" not in body
    assert body.startswith("#publi")


def test_real_delivery_with_mock_telegram_and_direct_link(tmp_path):
    settings = _settings(
        tmp_path, dry_run=False, telegram_real_enabled=True,
        telegram_bot_token="dummy", telegram_chat_id="@publicchannel",
        channel_visibility={"telegram": "public"},
    )
    db = Database(settings.database_path)
    db.init()
    batch = prepare_assisted_offers("shopee", [_item()], terms=("ssd",))
    posted = []
    class FakeTelegram:
        def send_photo(self, body, photo):
            posted.append((body, photo))
            return type("Result", (), {"dry_run": False, "message_id": "9001"})()
        def send_message(self, body):
            raise AssertionError("Should use product photo")
    p = Pipeline(db, settings, telegram=FakeTelegram(), source_adapter="assisted")
    ids = p.ingest_offers(batch.offers, content_campaign_id="campaign-real")
    p.curate_and_queue(ids, "campaign-real")
    result = p.process_one()
    assert result["status"] == "PUBLISHED"
    assert len(posted) == 1
    assert "sub_id=camp1" in posted[0][0]
    assert posted[0][1].startswith("https://down-br.img.susercontent.com/")
    assert db.rows("SELECT COUNT(*) AS n FROM publications")[0]["n"] == 1
    assert p.process_one() is None


def test_real_rate_cap_defer_second_offer_without_claiming_queue(tmp_path):
    settings = _settings(
        tmp_path, dry_run=False, telegram_real_enabled=True,
        telegram_bot_token="dummy", telegram_chat_id="@publicchannel",
        channel_visibility={"telegram": "public"}, telegram_max_posts_per_hour=1,
    )
    db = Database(settings.database_path)
    db.init()
    sent = []
    class Sender:
        def send_message(self, text):
            sent.append(text)
            return type("R", (), {"dry_run": False, "message_id": str(len(sent))})()
        def send_photo(self, text, photo):
            return self.send_message(text)
    pipe = Pipeline(db, settings, telegram=Sender(), source_adapter="assisted")
    first = _item()
    second = _item()
    second["title"] = "Mouse Gamer Sem Fio"
    second["external_product_id"] = "tech-456"
    second["source_url"] = "https://shopee.com.br/product/1/tech-456"
    second["affiliate_url"] = "https://s.shopee.com.br/tech-456"
    ids = pipe.ingest_offers(
        prepare_assisted_offers("shopee", [first, second], terms=("ssd", "mouse")).offers,
        content_campaign_id="real-two-items",
    )
    assert len(pipe.curate_and_queue(ids, "real-two-items")) == 2
    assert pipe.process_one()["status"] == "PUBLISHED"
    assert pipe.process_one() is None
    assert len(sent) == 1
    assert db.queue_depths(dry_run=False)["publish_queue_depth"] == 1


def test_telegram_only_worker_skips_legacy_artificial_media(tmp_path):
    from app.worker import Worker
    from app.scheduler import run_database_tick
    settings = _settings(tmp_path)
    db = Database(settings.database_path)
    db.init()
    batch = prepare_assisted_offers("shopee", [_item()], terms=("ssd",))
    pipe = Pipeline(db, settings, source_adapter="assisted")
    pipe.ingest_offers(batch.offers, content_campaign_id="no-media")
    class ForbiddenMedia:
        def generate_offer(self, offer_id, campaign):
            raise AssertionError("legacy video rendering must not run")
    worker = Worker(
        db, settings, pipe, ForbiddenMedia(), None, idle_seconds=1,
        error_backoff_seconds=1, tick=run_database_tick,
    )
    worker._process_content_jobs({"status": "idle"})
    assert db.queue_depths(dry_run=True)["publish_queue_depth"] == 1
    assert db.rows("SELECT COUNT(*) AS n FROM social_queue")[0]["n"] == 0
