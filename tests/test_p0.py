from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import app.adapters.local_files as local_files
import app.services.conversions as conversions_service
from fastapi.testclient import TestClient

from app.adapters.base import Capability, CapabilityStatus
from app.adapters.shopee_manual import ShopeeManualAdapter
from app.config import Settings
from app.db import Database
from app.models import Offer, utc_now
from app.scheduler import due_slot, run_due, run_tick
from app.services.compliance import ComplianceError, validate_content, validate_offer
from app.services.content import telegram_message, telegram_tracking_url
from app.services.conversions import import_conversion_csv
from app.services.curation import score_offer, verified_offer_data
from app.services.dedup import is_in_cooldown
from app.services.pipeline import Pipeline
from app.services.telegram import TelegramClient, TelegramResult
from app.services.site import offer_slug
from app.web import create_app


def settings_for(tmp_path: Path, *, dry_run: bool = True) -> Settings:
    return Settings(
        database_path=tmp_path / "test.db",
        dry_run=dry_run,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.init()
    return database


def write_offer_csv(path: Path, *, affiliate_url: str = "https://s.shopee.com.br/abc?sub_id=canal-1") -> Path:
    fields = {
        "external_product_id": "sku-1",
        "title": "Fone de ouvido",
        "description": "Exemplo",
        "category": "Eletronicos",
        "brand": "Marca",
        "original_price": "199,90",
        "current_price": "99,90",
        "coupon": "CUPOM10",
        "coupon_expiration": "2099-12-31T00:00:00Z",
        "shipping": "Frete gratis",
        "rating": "4.8",
        "sales_count": "5000",
        "commission_rate": "20",
        "image_urls": "https://down-br.img.susercontent.com/a.jpg",
        "source_url": "https://shopee.com.br/product/1/sku-1",
        "affiliate_url": affiliate_url,
        "stock_status": "IN_STOCK",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(fields)
    return path


def insert_offer(db: Database) -> int:
    return db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="sku-direct",
        title="Produto",
        category="Eletronicos",
        original_price_cents=20000,
        current_price_cents=9000,
        discount_percent=55,
        coupon="CUPOM",
        shipping="Frete gratis",
        rating=4.9,
        sales_count=10000,
        commission_rate=15,
        source_url="https://shopee.com.br/product/1/2",
        affiliate_url="https://s.shopee.com.br/x?sub_id=original&foo=1",
        stock_status="IN_STOCK",
    ))


def insert_second_offer(db: Database) -> int:
    return db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="sku-second",
        title="Segundo produto",
        category="Eletronicos",
        original_price_cents=18000,
        current_price_cents=8000,
        discount_percent=55,
        coupon="CUPOM",
        shipping="Frete gratis",
        rating=4.9,
        sales_count=10000,
        commission_rate=15,
        source_url="https://shopee.com.br/product/1/3",
        affiliate_url="https://s.shopee.com.br/y?sub_id=original",
        stock_status="IN_STOCK",
    ))


def test_manual_feed_reader_bounds_shopee_csv_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = write_offer_csv(tmp_path / "offers.csv")
    monkeypatch.setattr(local_files, "MAX_LOCAL_FEED_BYTES", 10)
    with pytest.raises(ValueError, match="arquivo Shopee excede limite"):
        ShopeeManualAdapter().import_offers(csv_path)

    monkeypatch.setattr(local_files, "MAX_LOCAL_FEED_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(local_files, "MAX_LOCAL_FEED_ROWS", 1)
    duplicated = tmp_path / "two.csv"
    duplicated.write_text(
        csv_path.read_text(encoding="utf-8") + csv_path.read_text(encoding="utf-8").splitlines()[-1] + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="excede limite de 1 linhas"):
        ShopeeManualAdapter().import_offers(duplicated)

    malformed = tmp_path / "offers.json"
    malformed.write_text('{"offers":"not-a-list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="lista de ofertas"):
        ShopeeManualAdapter().import_offers(malformed)


def test_shopee_adapter_declares_truthful_capabilities_and_normalizes(tmp_path: Path) -> None:
    adapter = ShopeeManualAdapter()
    offer = adapter.import_offers(write_offer_csv(tmp_path / "offers.csv"))[0]
    assert adapter.integration_status == CapabilityStatus.MANUAL_OR_PENDING
    assert adapter.capabilities[Capability.CREATE_AFFILIATE_LINK] == CapabilityStatus.MANUAL
    assert adapter.capabilities[Capability.DISCOVER_DEALS] == CapabilityStatus.WAITING_FOR_CREDENTIALS
    assert offer.current_price_cents == 9990
    assert offer.original_price_cents is None
    assert offer.discount_percent is None
    assert offer.tracking_metadata == {
        "sub_id_from_official_link": "canal-1",
        "unverified_reference_price_cents": 19990,
    }


def test_shopee_adapter_ignores_malformed_optional_metrics(tmp_path: Path) -> None:
    source = write_offer_csv(tmp_path / "offers.csv")
    text = source.read_text(encoding="utf-8")
    source.write_text(
        text.replace("4.8,5000,20,", "invalid-rating,invalid-sales,invalid-rate,"),
        encoding="utf-8",
    )
    offer = ShopeeManualAdapter().import_offers(source)[0]
    assert offer.rating is None
    assert offer.sales_count is None
    assert offer.commission_rate is None


def test_adapter_rejects_non_shopee_affiliate_url(tmp_path: Path) -> None:
    source = write_offer_csv(tmp_path / "offers.csv", affiliate_url="https://example.com/redirect")
    with pytest.raises(ValueError, match="oficial da Shopee"):
        ShopeeManualAdapter().import_offers(source)


def test_score_is_deterministic_and_ignores_unverified_discount(db: Database) -> None:
    offer_id = insert_offer(db)
    score, classification = score_offer(db, offer_id)
    assert score == 50
    assert classification == "GOOD"
    assert score_offer(db, offer_id) == (score, classification)


def test_content_disclosure_is_first_and_compliance_blocks_missing_disclosure(db: Database) -> None:
    offer = dict(db.get_offer(insert_offer(db)))
    body = telegram_message(offer, "https://example.test/go/1")
    assert body.splitlines()[0] == "#publi"
    validate_content(body)
    with pytest.raises(ComplianceError, match="publicitaria"):
        validate_content("Oferta sem aviso")
    with pytest.raises(ComplianceError, match="HTTP inseguro"):
        validate_content("#publi\nhttp://example.test/go/1")
    with pytest.raises(ComplianceError, match="4096"):
        validate_content("#publi\n" + ("x" * 4096), max_chars=4096)


def test_telegram_oversized_content_is_rejected_before_queue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    with db.connect() as connection:
        connection.execute("UPDATE offers SET title=? WHERE id=?", ("X" * 5000, offer_id))

    queued = Pipeline(db, settings).curate_and_queue([offer_id], "oversized")

    assert queued == []
    assert db.rows("SELECT id FROM content_packages") == []
    assert db.rows("SELECT id FROM publish_queue") == []


def test_compliance_blocks_out_of_stock() -> None:
    with pytest.raises(ComplianceError, match="indisponivel"):
        validate_offer({
            "title": "Produto",
            "source_url": "https://shopee.com.br/a",
            "affiliate_url": "https://s.shopee.com.br/a",
            "current_price_cents": 100,
            "stock_status": "OUT_OF_STOCK",
        })


def test_full_pipeline_dry_run_is_simulated_and_does_not_create_cooldown(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    result = Pipeline(db, settings).run(write_offer_csv(tmp_path / "offers.csv"), "test")
    assert result["publications"][0]["status"] == "SIMULATED"
    assert db.overview()["publications"] == 0
    assert db.overview()["simulations"] == 1
    assert not is_in_cooldown(db, result["offers"][0], "telegram")
    body = db.rows("SELECT body FROM content_packages WHERE channel='telegram' ORDER BY id DESC LIMIT 1")[0]["body"]
    tracking = urlsplit(body.splitlines()[-1])
    assert TestClient(create_app(settings, db)).get(
        f"{tracking.path}?{tracking.query}", follow_redirects=False,
    ).status_code == 404
    assert db.publication_count(result["offers"][0]) == 0


class SuccessTelegram:
    def send_message(self, text: str) -> TelegramResult:
        return TelegramResult(message_id="42", dry_run=False)


class DrySuccessTelegram:
    def send_message(self, text: str) -> TelegramResult:
        return TelegramResult(message_id="dry-42", dry_run=True)


class FailingTelegram:
    def send_message(self, text: str) -> TelegramResult:
        raise RuntimeError("falha temporaria")


def test_real_and_dry_run_have_separate_idempotency_and_real_deduplicates(tmp_path: Path) -> None:
    dry_settings = settings_for(tmp_path)
    db = Database(dry_settings.database_path)
    db.init()
    source = write_offer_csv(tmp_path / "offers.csv")
    dry = Pipeline(db, dry_settings).run(source, "same")
    real_settings = replace(dry_settings, dry_run=False, public_base_url="https://offers.example")
    real = Pipeline(db, real_settings, telegram=SuccessTelegram()).run(source, "same")
    assert dry["publications"][0]["status"] == "SIMULATED"
    assert real["publications"][0]["status"] == "PUBLISHED"
    assert db.overview()["publications"] == 1
    assert db.overview()["simulations"] == 1
    assert is_in_cooldown(db, real["offers"][0], "telegram")
    assert Pipeline(db, real_settings, telegram=SuccessTelegram()).run(source, "same")["queue"] == []


def test_category_cooldown_prevents_channel_domination(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), dry_run=False, public_base_url="https://offers.example")
    db = Database(settings.database_path)
    db.init()
    first = insert_offer(db)
    second = insert_second_offer(db)
    pipeline = Pipeline(db, settings, telegram=SuccessTelegram())
    assert pipeline.curate_and_queue([first], "campaign-a")
    assert pipeline.process_one()["status"] == "PUBLISHED"
    assert is_in_cooldown(db, second, "telegram", "campaign-b")


def test_tracking_records_click_and_preserves_affiliate_url_exactly(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    client = TestClient(create_app(settings, db))
    response = client.get(
        f"/go/{offer_id}?channel=telegram&campaign_id=c1&creative_id=k1&utm_source=tg",
        follow_redirects=False,
        headers={"referer": "https://t.me/canal"},
    )
    assert response.status_code == 302
    assert response.headers["location"] == "https://s.shopee.com.br/x?sub_id=original&foo=1"
    click = db.rows("SELECT * FROM clicks")[0]
    assert click["channel"] == "telegram"
    assert click["utm_source"] == "tg"
    assert click["sub_id"] == "original"
    assert db.overview()["conversions"] == 0


def test_tracking_bounds_untrusted_referer_header(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    client = TestClient(create_app(settings, db))

    response = client.get(
        f"/go/{offer_id}?channel=site&campaign_id=referer-test&creative_id=bounded",
        follow_redirects=False,
        headers={"referer": "https://example.test/" + ("x" * 5000)},
    )

    assert response.status_code == 302
    click = db.rows("SELECT referrer FROM clicks")[0]
    assert len(click["referrer"]) == 2048


def test_published_telegram_link_keeps_its_affiliate_destination_after_new_import(tmp_path: Path) -> None:
    settings = replace(
        settings_for(tmp_path), dry_run=False, public_base_url="https://offers.example", admin_password="test",
    )
    db = Database(settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    client = TestClient(create_app(settings, db))
    pipeline = Pipeline(db, settings, telegram=SuccessTelegram())
    queue_id = pipeline.curate_and_queue([offer_id], "old-campaign")[0]
    body = db.rows("SELECT c.body FROM content_packages c JOIN publish_queue q ON q.content_package_id=c.id WHERE q.id=?", (queue_id,))[0]["body"]
    tracking = urlsplit(body.splitlines()[-1])
    target = f"{tracking.path}?{tracking.query}"
    key = parse_qs(tracking.query)["publication_key"][0]
    assert client.get(target, follow_redirects=False).status_code == 404
    assert pipeline.process_one()["status"] == "PUBLISHED"

    with db.connect() as connection:
        connection.execute(
            "UPDATE offers SET affiliate_url='https://s.shopee.com.br/new?sub_id=new' WHERE id=?",
            (offer_id,),
        )
    click = client.get(target, follow_redirects=False)
    assert click.status_code == 302
    assert click.headers["location"] == "https://s.shopee.com.br/x?sub_id=original&foo=1"
    assert client.get(target.replace("old-campaign", "other-campaign"), follow_redirects=False).status_code == 404
    assert client.get(target.replace(key, "0" * 64), follow_redirects=False).status_code == 404
    assert client.get(f"/go/{offer_id}?channel=site", follow_redirects=False).headers["location"] == "https://s.shopee.com.br/new?sub_id=new"
    clicks = db.rows("SELECT channel,campaign_id,sub_id FROM clicks ORDER BY rowid")
    assert len(clicks) == 2
    assert clicks[0]["channel"] == "telegram"
    assert clicks[0]["campaign_id"] == "old-campaign"
    assert clicks[0]["sub_id"] == "original"
    with db.connect() as connection:
        connection.execute("UPDATE offers SET stock_status='OUT_OF_STOCK' WHERE id=?", (offer_id,))
    assert client.get(target, follow_redirects=False).status_code == 410
    assert len(db.rows("SELECT click_id FROM clicks")) == 2


@pytest.mark.parametrize("column,value", [
    ("stock_status", "OUT_OF_STOCK"),
    ("expires_at", "2000-01-01T00:00:00Z"),
    ("coupon_expiration", "2000-01-01T00:00:00Z"),
])
def test_unavailable_offer_never_redirects_or_records_click(tmp_path: Path, column: str, value: str) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    client = TestClient(create_app(settings, db))
    offer_id = insert_offer(db)
    statements = {
        "stock_status": "UPDATE offers SET stock_status=? WHERE id=?",
        "expires_at": "UPDATE offers SET expires_at=? WHERE id=?",
        "coupon_expiration": "UPDATE offers SET coupon_expiration=? WHERE id=?",
    }
    with db.connect() as connection:
        connection.execute(statements[column], (value, offer_id))

    assert client.get(f"/go/{offer_id}?channel=telegram", follow_redirects=False).status_code == 410
    assert db.rows("SELECT click_id FROM clicks") == []
    page = client.get(f"/o/{offer_slug(db.get_offer(offer_id))}")
    assert page.status_code == 200
    assert "Ir para a oferta" not in page.text
    assert "Preço aguardando atualização" in client.get("/offers").text


def test_conversion_csv_import_is_atomic_when_later_row_is_invalid(
    db: Database, tmp_path: Path,
) -> None:
    path = tmp_path / "atomic-conversions.csv"
    path.write_text(
        "external_order_id,status,value,commission\n"
        "order-valid,APPROVED,100.00,10.00\n"
        "order-invalid,NOT_A_STATUS,200.00,20.00\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="status de conversao invalido"):
        import_conversion_csv(db, path)

    assert db.rows("SELECT id FROM conversions") == []


def test_conversion_csv_limits_fail_closed_without_partial_import(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bounded-conversions.csv"
    path.write_text(
        "external_order_id,status,value,commission\n"
        "order-1,APPROVED,100.00,10.00\n"
        "order-2,APPROVED,200.00,20.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(conversions_service, "MAX_CONVERSION_ROWS", 1)
    with pytest.raises(ValueError, match="excede limite de 1 linhas"):
        import_conversion_csv(db, path)
    assert db.rows("SELECT id FROM conversions") == []

    monkeypatch.setattr(conversions_service, "MAX_CONVERSION_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="arquivo de conversoes excede limite"):
        import_conversion_csv(db, path)
    assert db.rows("SELECT id FROM conversions") == []


def test_conversion_csv_requires_unambiguous_headers_and_normalizes_blank_defaults(
    db: Database, tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-header.csv"
    missing.write_text("status,value\nAPPROVED,10.00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="external_order_id"):
        import_conversion_csv(db, missing)

    duplicate = tmp_path / "duplicate-header.csv"
    duplicate.write_text(
        "external_order_id,status,status\norder-1,APPROVED,APPROVED\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cabecalhos duplicados"):
        import_conversion_csv(db, duplicate)

    normalized = tmp_path / "normalized-fields.csv"
    normalized.write_text(
        "external_order_id,status,merchant,network,timestamp\n"
        "order-1,APPROVED,   ,   ,   \n",
        encoding="utf-8",
    )
    assert import_conversion_csv(db, normalized) == 1
    row = db.rows(
        "SELECT merchant,network,timestamp FROM conversions WHERE external_order_id='order-1'"
    )[0]
    assert row["merchant"] == "Shopee"
    assert row["network"] == "Shopee Afiliados"
    assert row["timestamp"]


def test_conversion_import_is_idempotent_and_updates_analytics(db: Database, tmp_path: Path) -> None:
    offer_id = insert_offer(db)
    path = tmp_path / "conversions.csv"
    path.write_text(
        "external_order_id,offer_id,merchant,network,value,commission,status,channel,campaign,timestamp\n"
        f"order-1,{offer_id},Shopee,Shopee Afiliados,100.00,12.50,APPROVED,telegram,c1,{utc_now()}\n",
        encoding="utf-8",
    )
    assert import_conversion_csv(db, path) == 1
    assert import_conversion_csv(db, path) == 1
    overview = db.overview()
    assert overview["conversions"] == 1
    assert overview["revenue_cents"] == 10000
    assert overview["commission_cents"] == 1250
    assert overview["impressions"] is None


def test_conversion_click_attribution_rejects_conflict_and_infers_missing_fields(db: Database) -> None:
    offer_id = insert_offer(db)
    other_offer_id = insert_second_offer(db)
    click_id = db.record_click(
        db.get_offer(offer_id),
        channel="telegram",
        campaign_id="c1",
        creative_id="k1",
        referrer=None,
        utm_source=None,
        utm_medium=None,
        utm_campaign=None,
        sub_id=None,
    )
    conversion = {
        "external_order_id": "order-with-click",
        "offer_id": other_offer_id,
        "click_id": click_id,
        "merchant": "Shopee",
        "network": "Shopee Afiliados",
        "value_cents": 10000,
        "commission_cents": 1250,
        "status": "APPROVED",
        "channel": None,
        "campaign": None,
        "timestamp": utc_now(),
    }
    with pytest.raises(ValueError, match="oferta.*clique"):
        db.import_conversion(conversion)
    assert db.rows("SELECT id FROM conversions") == []

    db.import_conversion({**conversion, "offer_id": None})
    stored = db.rows("SELECT offer_id,channel,campaign FROM conversions")[0]
    assert stored["offer_id"] == offer_id
    assert stored["channel"] == "telegram"
    assert stored["campaign"] == "c1"

    db.import_conversion({**conversion, "external_order_id": " order-with-click ", "offer_id": None})
    assert len(db.rows("SELECT id FROM conversions")) == 1

    with pytest.raises(ValueError, match="ID externo"):
        db.import_conversion({**conversion, "external_order_id": " ", "offer_id": None})

    with pytest.raises(ValueError, match="canal.*clique"):
        db.import_conversion({**conversion, "offer_id": offer_id, "channel": "instagram"})
    assert len(db.rows("SELECT id FROM conversions")) == 1


def test_retry_backoff_and_circuit_breaker_after_three_failures(tmp_path: Path) -> None:
    settings = replace(settings_for(tmp_path), dry_run=False, public_base_url="https://offers.example")
    db = Database(settings.database_path)
    db.init()
    pipeline = Pipeline(db, settings, telegram=FailingTelegram())
    ids = pipeline.ingest_shopee(write_offer_csv(tmp_path / "offers.csv"))
    pipeline.curate_and_queue(ids, "fail")
    for _ in range(3):
        result = pipeline.process_one()
        assert result and result["status"] == "FAILED"
        with db.connect() as connection:
            connection.execute("UPDATE publish_queue SET available_at='2000-01-01T00:00:00+00:00' WHERE status='FAILED'")
    state = db.channel_state("telegram")
    assert state["consecutive_failures"] == 3
    assert state["open_until"] is not None


def test_queue_claim_is_strictly_isolated_between_dry_and_real_modes(tmp_path: Path) -> None:
    dry_settings = settings_for(tmp_path)
    real_settings = replace(dry_settings, dry_run=False, public_base_url="https://offers.example")
    db = Database(dry_settings.database_path)
    db.init()
    offer_id = insert_offer(db)
    real_body = telegram_message(
        verified_offer_data(db, offer_id),
        telegram_tracking_url(real_settings.public_base_url, offer_id, "real"),
    )
    real_content = db.add_content(offer_id, "telegram", real_body)
    real_queue = db.enqueue(offer_id, real_content, "telegram", "real", "real-creative", "real-key", dry_run=False)

    dry_pipeline = Pipeline(db, dry_settings, telegram=DrySuccessTelegram())
    assert dry_pipeline.process_one() is None
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (real_queue,))[0]["status"] == "PENDING"

    dry_body = telegram_message(
        verified_offer_data(db, offer_id),
        telegram_tracking_url(dry_settings.public_base_url, offer_id, "dry"),
    )
    dry_content = db.add_content(offer_id, "telegram", dry_body)
    dry_queue = db.enqueue(offer_id, dry_content, "telegram", "dry", "dry-creative", "dry-key", dry_run=True)
    real_pipeline = Pipeline(db, real_settings, telegram=SuccessTelegram())
    assert real_pipeline.process_one()["status"] == "PUBLISHED"
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (dry_queue,))[0]["status"] == "PENDING"
    assert dry_pipeline.process_one()["status"] == "SIMULATED"


def test_dry_run_does_not_read_or_reset_real_circuit_breaker(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    db.note_channel_failure("telegram", None)
    db.note_channel_failure("telegram", None)
    offer_id = insert_offer(db)
    body = telegram_message(
        verified_offer_data(db, offer_id),
        telegram_tracking_url(settings.public_base_url, offer_id, "dry"),
    )
    content_id = db.add_content(offer_id, "telegram", body)
    db.enqueue(offer_id, content_id, "telegram", "dry", "dry", "dry-circuit-key", dry_run=True)
    assert Pipeline(db, settings, telegram=DrySuccessTelegram()).process_one()["status"] == "SIMULATED"
    state = db.channel_state("telegram")
    assert state["consecutive_failures"] == 2
    assert state["open_until"] is None


def test_legacy_queue_migration_defaults_ambiguous_rows_to_dry_run(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE publish_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL,
            content_package_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            creative_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
    db = Database(path)
    db.init()
    columns = {row["name"] for row in db.rows("PRAGMA table_info(publish_queue)")}
    assert "dry_run" in columns
    assert "affiliate_url_snapshot" in columns


def test_failure_never_persists_telegram_token(tmp_path: Path) -> None:
    secret = "telegram-secret-value"
    settings = replace(settings_for(tmp_path), telegram_bot_token=secret)
    db = Database(settings.database_path)
    db.init()

    class SecretFailingTelegram:
        def send_message(self, text: str):
            raise RuntimeError(f"request to bot{secret}/sendMessage failed")

    pipeline = Pipeline(db, settings, telegram=SecretFailingTelegram())
    ids = pipeline.ingest_shopee(write_offer_csv(tmp_path / "offers.csv"))
    pipeline.curate_and_queue(ids, "secret")
    result = pipeline.process_one()
    assert secret not in result["error"]
    assert secret not in db.rows("SELECT last_error FROM publish_queue")[0]["last_error"]


def test_telegram_official_contract_parses_message_id() -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size: int = -1) -> bytes:
            return b'{"ok":true,"result":{"message_id":77}}'

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["timeout"] = timeout
        return FakeResponse()

    result = TelegramClient("secret", "@channel", dry_run=False, opener=opener).send_message("#publi\nOferta")
    assert result.message_id == "77"
    assert captured["url"].endswith("/sendMessage")
    assert b"chat_id=%40channel" in captured["body"]


def test_scheduler_uses_only_latest_elapsed_slot_after_long_pause() -> None:
    assert due_slot(datetime(2026, 9, 30, 6, 59)) is None
    assert due_slot(datetime(2026, 9, 30, 7, 6)) == "2026-09-30T07:00"
    assert due_slot(datetime(2026, 9, 30, 12, 59)) == "2026-09-30T10:00"
    assert due_slot(datetime(2026, 9, 30, 21, 30)) == "2026-09-30T19:00"
    assert due_slot(datetime(2026, 9, 30, 23, 59)) == "2026-09-30T22:00"


def test_scheduler_slot_is_claimed_once(db: Database, tmp_path: Path) -> None:
    assert due_slot(datetime(2026, 9, 30, 7, 3)) == "2026-09-30T07:00"

    class FakePipeline:
        def __init__(self, dry_run=True):
            self.calls = 0
            self.settings = SimpleNamespace(dry_run=dry_run)

        def run(self, source, campaign_id):
            self.calls += 1
            return {"campaign": campaign_id}

    pipeline = FakePipeline()
    now = datetime(2026, 9, 30, 7, 3)
    assert run_due(db, pipeline, tmp_path / "x.csv", now) is not None
    assert run_due(db, pipeline, tmp_path / "x.csv", now) is None
    assert pipeline.calls == 1
    real_pipeline = FakePipeline(dry_run=False)
    assert run_due(db, real_pipeline, tmp_path / "x.csv", now) is not None
    assert run_due(db, real_pipeline, tmp_path / "x.csv", now) is None
    assert real_pipeline.calls == 1

    class FailingPipeline:
        def __init__(self):
            self.calls = 0
            self.settings = SimpleNamespace(dry_run=True)

        def run(self, source, campaign_id):
            self.calls += 1
            raise RuntimeError("fonte indisponivel")

    failing = FailingPipeline()
    failing_now = datetime(2026, 10, 1, 7, 3)
    with pytest.raises(RuntimeError, match="indisponivel"):
        run_due(db, failing, tmp_path / "x.csv", failing_now)
    with pytest.raises(RuntimeError, match="indisponivel"):
        run_due(db, failing, tmp_path / "x.csv", failing_now)
    assert failing.calls == 2


def test_scheduler_catches_up_latest_slot_then_returns_to_queue_work(
    db: Database, tmp_path: Path,
) -> None:
    class RetryPipeline:
        def __init__(self):
            self.settings = SimpleNamespace(dry_run=True)
            self.run_calls = 0
            self.process_calls = 0

        def run(self, source, campaign_id):
            self.run_calls += 1
            return {"campaign_id": campaign_id}

        def process_one(self):
            self.process_calls += 1
            return {"status": "SIMULATED", "queue_id": 7}

    pipeline = RetryPipeline()
    now = datetime(2026, 9, 30, 8, 0)

    first = run_tick(db, pipeline, tmp_path / "offers.csv", now)
    assert first["status"] == "cycle"
    assert first["result"]["campaign_id"] == "scheduled-2026-09-30T07:00"
    assert pipeline.run_calls == 1
    assert pipeline.process_calls == 0

    second = run_tick(db, pipeline, tmp_path / "offers.csv", now)
    assert second == {"status": "queue", "result": {"status": "SIMULATED", "queue_id": 7}}
    assert pipeline.run_calls == 1
    assert pipeline.process_calls == 1


def test_invalid_dry_run_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "tru")
    with pytest.raises(ValueError, match="DRY_RUN deve ser true ou false"):
        Settings.from_env()
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://offers.example")
    assert Settings.from_env().dry_run is False


def test_dashboard_and_overview_are_available(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    client = TestClient(create_app(settings, db))
    assert client.get("/health").json() == {"status": "ok", "dry_run": True}
    assert client.get("/api/overview").json()["clicks"] == 0
    assert "Operacao de afiliados" in client.get("/").text
