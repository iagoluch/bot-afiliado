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
        "buy_now_enabled": True,
    }
    p.update(overrides)
    return {"action": "queue", "affiliate_url": "https://meli.la/2MWiFPG", "product": p}


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
    assert second == {"status": "ALREADY_QUEUED", "queued": 0}
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
