"""Regressoes: worker Telegram-only real dispensa P1 e URL publica HTTPS."""
from __future__ import annotations

from dataclasses import replace
from threading import Event

import pytest

from app import worker as worker_module
from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.services.collection import prepare_assisted_offers
from app.services.pipeline import Pipeline
from app.worker import Worker, _content_pipeline_for_worker


def _real_settings(tmp_path):
    return Settings(
        database_path=tmp_path / "real-telegram.db",
        dry_run=False,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token="fake-test-token",
        telegram_chat_id="@grupo_publico_teste",
        telegram_only_mode=True,
        telegram_real_enabled=True,
        channel_visibility={"telegram": "public"},
        creatives_path=tmp_path / "assets",
    )


def test_no_p1_constructed_for_real_telegram_only(tmp_path, monkeypatch):
    settings = _real_settings(tmp_path)
    db = Database(settings.database_path)
    db.init()
    def forbidden_p1(*args, **kwargs):
        pytest.fail("P1 must not initialize in real Telegram-only")
    monkeypatch.setattr(worker_module, "P1Pipeline", forbidden_p1)
    assert _content_pipeline_for_worker(db, settings) is None


def test_legacy_real_still_requires_public_https(tmp_path):
    settings = replace(_real_settings(tmp_path), telegram_only_mode=False)
    db = Database(settings.database_path)
    db.init()
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL precisa ser HTTPS"):
        _content_pipeline_for_worker(db, settings)


def test_real_telegram_only_worker_processes_reviewed_affiliate_with_no_p1(tmp_path):
    settings = _real_settings(tmp_path)
    db = Database(settings.database_path)
    db.init()
    posted = []
    class FakeTelegram:
        def send_message(self, body):
            posted.append(body)
            return type("TelegramResult", (), {"dry_run": False, "message_id": "test-42"})()

    pipeline = Pipeline(db, settings, telegram=FakeTelegram(), source_adapter="assisted")
    records = [{
        "external_product_id": "MLB4587377807",
        "title": "Fone de ouvido Bluetooth Dapon H02D",
        "category": "audio",
        "current_price": "74.90",
        "source_url": "https://www.mercadolivre.com.br/p/MLB24117280",
        "affiliate_url": "https://meli.la/2MWiFPG",
        "collected_at": utc_now(),
        "stock_status": "IN_STOCK",
        "manual_review_confirmed": True,
    }]
    batch = prepare_assisted_offers("mercadolivre", records, terms=("fone",))
    ids = pipeline.ingest_offers(batch.offers, content_campaign_id="first-verified")
    assert len(ids) == 1

    job_worker = Worker(
        db, settings, pipeline, _content_pipeline_for_worker(db, settings),
        None, idle_seconds=1, error_backoff_seconds=1, stop_event=Event(),
    )
    job_worker._process_content_jobs({"status": "idle"})
    result = pipeline.process_one()
    assert result is not None and result["status"] == "PUBLISHED"
    assert len(posted) == 1
    assert "https://meli.la/2MWiFPG" in posted[0]
    assert "/go/" not in posted[0]
    assert db.rows("SELECT COUNT(*) AS n FROM publications WHERE dry_run=0")[0]["n"] == 1
