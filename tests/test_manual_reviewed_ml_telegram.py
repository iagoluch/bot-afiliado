from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.services.collection import prepare_assisted_offers
from app.services.pipeline import Pipeline


AFFILIATE = "https://meli.la/2MWiFPG"
ORIGINAL = "https://www.mercadolivre.com.br/p/MLB27880616"


def settings(tmp_path, **changes):
    base = Settings(
        database_path=tmp_path / "affiliate.db",
        dry_run=True, public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None, telegram_chat_id=None,
        telegram_only_mode=True, creatives_path=tmp_path / "images",
    )
    return replace(base, **changes)


def record(*, reviewed=True, stock="IN_STOCK", collected_at=None):
    return {
        "external_product_id": "MLB27880616",
        "title": "SSD NVMe 1TB",
        "current_price": "279.90",
        "source_url": ORIGINAL,
        "affiliate_url": AFFILIATE,
        "stock_status": stock,
        "category": "informatica",
        "manual_review_confirmed": reviewed,
        "collected_at": collected_at or utc_now(),
    }


def build(tmp_path, data, **settings_changes):
    config = settings(tmp_path, **settings_changes)
    db = Database(config.database_path)
    db.init()
    batch = prepare_assisted_offers("mercadolivre", [data], terms=("ssd",))
    offer_id = db.upsert_offer(batch.offers[0])
    pipeline = Pipeline(db, config)
    return db, pipeline, offer_id


def test_reviewed_manual_link_queues_without_fake_rating(tmp_path):
    db, pipeline, offer_id = build(tmp_path, record())
    row = db.get_offer(offer_id)
    assert row["rating"] is None
    assert row["commission_rate"] is None
    queued = pipeline.curate_and_queue([offer_id], "first-verified-ml")
    assert len(queued) == 1
    content = db.rows("SELECT body FROM content_packages WHERE channel='telegram'")[0]["body"]
    assert AFFILIATE in content
    assert content.startswith("#publi")
    assert "Desconto" not in content
    assert "BAIXOU" not in content


def test_unreviewed_manual_link_does_not_bypass_score(tmp_path):
    _, pipeline, offer_id = build(tmp_path, record(reviewed=False))
    assert pipeline.curate_and_queue([offer_id], "unreviewed") == []


def test_stock_unknown_does_not_bypass_score(tmp_path):
    _, pipeline, offer_id = build(tmp_path, record(stock="UNKNOWN"))
    assert pipeline.curate_and_queue([offer_id], "unknown-stock") == []


def test_stale_review_does_not_bypass_score(tmp_path):
    old=(datetime.now(timezone.utc)-timedelta(minutes=35)).isoformat()
    _, pipeline, offer_id = build(tmp_path, record(collected_at=old))
    assert pipeline.curate_and_queue([offer_id], "stale") == []


def test_not_telegram_only_does_not_bypass_score(tmp_path):
    _, pipeline, offer_id = build(tmp_path, record(), telegram_only_mode=False)
    assert pipeline.curate_and_queue([offer_id], "normal-mode") == []


def test_reviewed_link_real_mode_enqueues_without_network(tmp_path):
    db,pipeline,offer_id=build(
        tmp_path,record(),
        dry_run=False,telegram_real_enabled=True,
        telegram_bot_token="fake",telegram_chat_id="@tech_public",
        channel_visibility={"telegram":"public"},
    )
    queued=pipeline.curate_and_queue([offer_id],"first-real")
    assert len(queued) == 1
    rows=db.rows("SELECT body FROM content_packages WHERE channel='telegram'")
    assert AFFILIATE in rows[0]["body"]
    assert db.rows("SELECT COUNT(*) AS n FROM publications")[0]["n"] == 0
