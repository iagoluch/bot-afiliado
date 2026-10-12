from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.mercadolivre_autopilot import (
    MLError, _money_from_visible, _queue_validated_product,
    affiliate_from_text, normalize_product_url,
)
from app.services.pipeline import Pipeline


@pytest.mark.parametrize("url,expected", [
    (
        "https://www.mercadolivre.com.br/fone-dapon/p/MLB24117280?tracking_id=secret#pos=0",
        "https://www.mercadolivre.com.br/fone-dapon/p/MLB24117280",
    ),
    ("https://www.mercadolivre.com.br/p/MLB24117280","https://www.mercadolivre.com.br/p/MLB24117280"),
    ("https://produto.mercadolivre.com.br/MLB-4587377807-headphone-_JM",
     "https://produto.mercadolivre.com.br/MLB-4587377807-headphone-_JM"),
    ("http://www.mercadolivre.com.br/p/MLB24117280",None),
    ("https://mercadolivre.com.br.evil.example/p/MLB24117280",None),
    ("https://mercadolivre.com.br@evil.example/p/MLB24117280",None),
    ("https://evil.example/mlb24117280",None),
    ("https://www.mercadolivre.com.br/l/cupom",None),
])
def test_sources_canonicalized_and_other_hosts_blocked(url,expected):
    assert normalize_product_url(url)==expected


def test_extracts_one_real_official_affiliate_link_not_plain_product():
    assert affiliate_from_text("https://meli.la/2MWiFPG")=="https://meli.la/2MWiFPG"
    assert affiliate_from_text("https://meli.la/2MWiFPG\nhttps://meli.la/2MWiFPG")=="https://meli.la/2MWiFPG"
    assert affiliate_from_text("https://meli.la/AAA\nhttps://meli.la/BBB") is None
    assert affiliate_from_text("https://www.mercadolivre.com.br/p/MLB24117280") is None
    assert affiliate_from_text("https://meli.la.evil.test/AAA") is None
    assert affiliate_from_text("https://evil.test/?x=https://meli.la/AAA") is None


@pytest.mark.parametrize("text,expected", [
    ("74,90",7490),
    ("1.234,90",123490),
    ("74,9",None),
    ("R$ 74,90",None),
    ("",None),
    ("grátis",None),
    ("1e10000",None),
])
def test_visible_price_parsing(text,expected):
    assert _money_from_visible(text)==expected


def _settings(tmp_path,*, dry_run=False,telegram_only=True):
    return Settings(
        database_path=tmp_path / "ml-browser.db",
        dry_run=dry_run,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token="mock",
        telegram_chat_id="@publicchannel",
        channel_visibility={"telegram":"public"},
        telegram_only_mode=telegram_only,
        telegram_real_enabled=not dry_run,
    )


def _details(*, timestamp=None, price=7490, title="Headphone Dapon H02D Bluetooth"):
    return {
        "external_id":"MLB24117280",
        "source_url":"https://www.mercadolivre.com.br/p/MLB24117280",
        "title":title,"price_cents":price,
        "stock_status":"IN_STOCK",
        "confirmed_at":timestamp or utc_now(),
    }


def test_autopilot_validated_offer_queues_job_for_real_worker(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    result=_queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    assert result["status"]=="QUEUED_FOR_WORKER"
    assert result["published_now"]==0
    assert db.rows("SELECT COUNT(*) n FROM content_jobs WHERE dry_run=0")[0]["n"]==1
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0
    row=db.get_offer(result["offer_ids"][0])
    assert row["current_price_cents"]==7490
    assert row["source_type"]=="ML_BROWSER_VERIFIED"
    assert row["affiliate_url"]=="https://meli.la/2MWiFPG"
    pipe=Pipeline(db,settings)
    assert len(pipe.curate_and_queue(result["offer_ids"],"browser-real"))==1
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0


def test_autopilot_never_queues_invalid_link(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="LINK_NAO_AUTORIZADO"):
        _queue_validated_product(db,settings,_details(),"https://www.mercadolivre.com.br/p/MLB24117280")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_never_queues_old_price(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    old=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(timespec="seconds")
    with pytest.raises(MLError,match="EXPIRADA"):
        _queue_validated_product(db,settings,_details(timestamp=old),"https://meli.la/2MWiFPG")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_telegram_only_gate(tmp_path):
    settings=_settings(tmp_path,telegram_only=False)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="NAO_AUTORIZADO"):
        _queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    assert not db.rows("SELECT 1 FROM offers")


def test_autopilot_final_dispatch_rejects_expired_verification(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    result=_queue_validated_product(db,settings,_details(),"https://meli.la/2MWiFPG")
    oid=result["offer_ids"][0]
    pipe=Pipeline(db,settings)
    assert pipe.curate_and_queue([oid],"browser-expiry")
    with db.connect() as con:
        con.execute("UPDATE offers SET expires_at=? WHERE id=?",
                    ((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(timespec="seconds"),oid))
    result=pipe.process_one()
    assert result["status"]=="REJECTED_STALE"
    assert db.rows("SELECT COUNT(*) n FROM publications")[0]["n"]==0


def test_nontech_product_never_queues(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    with pytest.raises(MLError,match="FORA_DO_NICHO"):
        _queue_validated_product(db,settings,_details(title="Conjunto de panelas de inox"),
                                 "https://meli.la/2MWiFPG")
