from __future__ import annotations

import io
import json
import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.ml_chrome_bridge import _read_frame, _write_frame, process
from scripts.install_ml_chrome_bridge import HOST_NAME, install


def settings(tmp_path, *, real=False):
    return Settings(
        database_path=tmp_path / "bot.db",
        dry_run=not real,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token="fake",
        telegram_chat_id="@tech",
        channel_visibility={"telegram": "public"},
        telegram_only_mode=True,
        telegram_real_enabled=real,
    )


def record(**overrides):
    p = {
        "source_url": "https://www.mercadolivre.com.br/p/MLB24117280",
        "title": "Fone de Ouvido Dapon H02D Bluetooth 5.1",
        "price_cents": 7490,
        "price_evidence": "VISIBLE_DOM_PRICE_V2",
        "price_payment_method": "STANDARD",
        "other_price_cents": None,
        "buy_now_enabled": True,
    }
    p.update(overrides)
    return {"action": "queue", "affiliate_url": "https://meli.la/2MWiFPG", "topic": "audio", "product": p}


def test_ping_without_environment_access():
    assert process({"action": "ping"}) == {
        "status": "OK", "bridge": "ML_CHROME", "version": 1,
    }


def test_queue_official_affiliate_to_real_worker_without_instant_publish(tmp_path):
    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    reply = process(record(), settings=conf, db=db)
    assert reply["status"] == "QUEUED_FOR_WORKER"
    assert reply["queued"] == 1
    assert reply["published_now"] == 0
    assert db.rows("SELECT COUNT(*) n FROM offers")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) n FROM content_jobs")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"] == 0
    row = db.rows("SELECT * FROM offers")[0]
    assert row["affiliate_url"] == "https://meli.la/2MWiFPG"
    assert row["stock_status"] == "IN_STOCK"


def test_identical_pending_offer_not_enqueued_twice(tmp_path):
    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    assert process(record(), settings=conf, db=db)["queued"] == 1
    second = process(record(), settings=conf, db=db)
    assert second["status"] in {"ALREADY_QUEUED", "TOPIC_COOLDOWN"}
    assert db.rows("SELECT COUNT(*) n FROM content_jobs")[0]["n"] == 1


@pytest.mark.parametrize("changes", [
    {"source_url": "https://evil.invalid/p/MLB24117280"},
    {"source_url": "https://www.mercadolivre.com.br/p/MLB24117280?redir=true"},
    {"title": "Camiseta basica masculina"},
    {"price_cents": "74.90"},
    {"price_cents": 0},
    {"price_cents": True},
    {"buy_now_enabled": False},
])
def test_browser_observations_fail_closed_without_db_writes(tmp_path, changes):
    conf = settings(tmp_path)
    db = Database(conf.database_path)
    reply = process(record(**changes), settings=conf, db=db)
    assert reply["status"] == "BLOCKED"
    assert not db.rows("SELECT 1 FROM offers")


def test_fake_affiliate_link_rejected(tmp_path):
    conf = settings(tmp_path)
    db = Database(conf.database_path)
    payload = record()
    payload["affiliate_url"] = "https://mercadolivre.com.br/p/MLB24117280"
    assert process(payload, settings=conf, db=db)["status"] == "BLOCKED"
    assert not db.rows("SELECT 1 FROM offers")


@pytest.mark.parametrize("message", [
    [], "queue", 0, {}, {"action": "login"},
    {"action": "queue", "product": None, "affiliate_url": ""},
])
def test_no_sensitive_actions(message, tmp_path):
    assert process(message, settings=settings(tmp_path))["status"] == "BLOCKED"


def test_native_messaging_framing_binary_protocol():
    outgoing = io.BytesIO()
    _write_frame(outgoing, {"action": "ping"})
    raw = outgoing.getvalue()
    assert struct.unpack("=I", raw[:4])[0] == len(raw)-4
    parsed = _read_frame(io.BytesIO(raw))
    assert parsed == {"action": "ping"}


def test_frame_rejects_too_large_or_truncated():
    from app.ml_chrome_bridge import MAX_MESSAGE
    with pytest.raises(ValueError):
        _read_frame(io.BytesIO(struct.pack("=I", MAX_MESSAGE + 1)))
    with pytest.raises(ValueError):
        _read_frame(io.BytesIO(struct.pack("=I", 10) + b"{}"))


def test_installer_pins_only_selected_extension_and_does_not_need_root(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "project"
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").write_text("fake")
    eid = "abcdefghijklmnopabcdefghijklmnop"
    paths=install(eid, browser="both", home=home, root=root)
    assert len(paths)==2
    for path in paths:
        manifest=json.loads(path.read_text())
        assert manifest["name"]==HOST_NAME
        assert manifest["allowed_origins"] == ["chrome-extension://" + eid + "/"]
        assert manifest["type"]=="stdio"
        assert manifest["path"].startswith(str(home / ".local/bin/"))
        assert path.stat().st_mode & 0o077 == 0
    wrapper=home / ".local/bin/iago-ml-chrome-host"
    assert wrapper.stat().st_mode & 0o077 == 0
    assert "app.ml_chrome_bridge" in wrapper.read_text()
    assert "cookies" not in wrapper.read_text()


@pytest.mark.parametrize("invalid", [
    "", "a" * 31, "z" * 32, "../" + "a" * 32,
    "a" * 32 + ";rm -rf", "A" * 32,
])
def test_installer_rejects_malicious_extension_id(tmp_path, invalid):
    with pytest.raises(ValueError):
        install(invalid, home=tmp_path)


@pytest.mark.parametrize("changes", [
    {"price_evidence": None},
    {"price_evidence": "VISIBLE_DOM_PRICE_V1"},
    {"price_payment_method": None},
    {"price_payment_method": "UNKNOWN"},
    {"price_payment_method": "pix-no-discount"},
    {"other_price_cents": "900.00"},
    {"other_price_cents": 90000},
    {"other_price_cents": -1},
])
def test_reject_legacy_or_ambiguous_chrome_prices_even_in_real_mode(tmp_path, changes):
    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    result = process(record(**changes), settings=conf, db=db)
    assert result["status"] == "BLOCKED"
    assert not db.rows("SELECT 1 FROM offers")


def test_pix_discount_from_chrome_is_qualified_in_real_telegram_post(tmp_path):
    from app.services.pipeline import Pipeline

    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    payload = record(price_cents=75900, price_payment_method="PIX", other_price_cents=90000)
    result = process(payload, settings=conf, db=db)
    assert result["status"] == "QUEUED_FOR_WORKER"
    assert result["queued"] == 1

    offer_id = db.rows("SELECT id FROM offers ORDER BY id DESC LIMIT 1")[0]["id"]
    class FakeTelegram:
        def __init__(self):
            self.posts = []
        def send_message(self, message):
            self.posts.append(message)
            return type("Sent", (), {"message_id": "test-123", "dry_run": False})()

    client = FakeTelegram()
    pipeline = Pipeline(db, conf, telegram=client, source_adapter="assisted")
    assert pipeline.curate_and_queue([offer_id], "price-check")
    published = pipeline.process_one()
    assert published is not None and published["status"] == "PUBLISHED"
    assert len(client.posts) == 1
    assert "R$ 759,00 no Pix" in client.posts[0]
    assert "R$ 900,00 em outros meios de pagamento" in client.posts[0]
    assert "https://meli.la/2MWiFPG" in client.posts[0]


def test_standard_sale_price_without_pix_is_not_relabelled(tmp_path):
    from app.services.content import telegram_direct_message

    conf = settings(tmp_path)
    db = Database(conf.database_path)
    result = process(record(price_cents=75900), settings=conf, db=db)
    assert result["status"] == "QUEUED_FOR_WORKER"
    offer_id = db.rows("SELECT id FROM offers ORDER BY id DESC LIMIT 1")[0]["id"]
    from app.services.curation import verified_offer_data
    offer = verified_offer_data(db, offer_id)
    message = telegram_direct_message(offer)
    assert "R$ 759,00" in message
    assert "no Pix" not in message
    assert "R$ 900,00" not in message



def test_preflight_blocks_repeating_category_with_pending_job(tmp_path):
    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    assert process({"action": "topicStatus", "topic": "audio"}, settings=conf, db=db)["status"] == "OK"
    assert process(record(), settings=conf, db=db)["status"] == "QUEUED_FOR_WORKER"
    assert process({"action": "topicStatus", "topic": "audio"}, settings=conf, db=db)["status"] == "TOPIC_COOLDOWN"
    assert process({"action": "topicStatus", "topic": "ssd"}, settings=conf, db=db)["status"] == "OK"
    assert process({"action": "topicStatus", "topic": "cartao"}, settings=conf, db=db)["status"] == "BLOCKED"


def test_category_cooldown_rejects_second_different_product(tmp_path):
    conf = settings(tmp_path, real=True)
    db = Database(conf.database_path)
    assert process(record(), settings=conf, db=db)["status"] == "QUEUED_FOR_WORKER"
    variant = record(source_url="https://www.mercadolivre.com.br/p/MLB25117280")
    assert process(variant, settings=conf, db=db)["status"] == "TOPIC_COOLDOWN"
    assert db.rows("SELECT COUNT(*) n FROM content_jobs")[0]["n"] == 1
