from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app import cli
from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.scheduler import run_database_tick
from app.services.collection import (
    collection_status,
    prepare_assisted_offers,
    read_assisted_json,
)
from app.services.pipeline import Pipeline
from app.services.telegram import TelegramClient
from app.worker import Worker, _source_from_env


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "collection.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
    )


def shopee_record(
    *,
    title: str = "Notebook gamer",
    product_id: str = "tech-1",
    collected_at: str | None = None,
) -> dict:
    return {
        "external_product_id": product_id,
        "title": title,
        "category": "Informática" if "Notebook" in title else "Casa",
        "current_price": "2499.90",
        "source_url": f"https://shopee.com.br/product/1/{product_id}",
        "affiliate_url": f"https://shope.ee/{product_id}",
        "collected_at": collected_at or utc_now(),
        "stock_status": "IN_STOCK",
        "rating": "4.9",
        "sales_count": "5000",
        "commission_rate": "20",
        "coupon": "TECH10",
        "shipping": "frete gratis",
    }


def test_assisted_json_filters_non_technology_and_preserves_observation_time() -> None:
    records = read_assisted_json(
        io.StringIO(json.dumps([
            shopee_record(),
            shopee_record(title="Jogo de panelas", product_id="home-1"),
        ])),
    )

    records[0]["collected_at"] = "2026-10-11T15:00:00-03:00"
    batch = prepare_assisted_offers("shopee", records, terms=("notebook", "informatica"))

    assert len(batch.offers) == 1
    assert batch.rejected_non_technology == 1
    assert batch.offers[0].external_product_id == "tech-1"
    assert batch.offers[0].collected_at == "2026-10-11T15:00:00-03:00"
    assert batch.offers[0].expires_at == "2026-10-12T18:00:00+00:00"


def test_assisted_json_requires_explicit_collection_time_and_is_bounded() -> None:
    record = shopee_record()
    record.pop("collected_at")
    with pytest.raises(ValueError, match="collected_at"):
        prepare_assisted_offers("shopee", [record], terms=("notebook",))

    with pytest.raises(ValueError, match="limite"):
        read_assisted_json(io.StringIO(" " * (1024 * 1024 + 1)), max_bytes=1024 * 1024)


def test_generic_accessories_are_not_classified_as_technology() -> None:
    batch = prepare_assisted_offers(
        "shopee",
        [shopee_record(title="Acessórios femininos", product_id="fashion-1")],
        terms=("fone", "ssd", "notebook"),
    )

    assert batch.offers == []
    assert batch.rejected_non_technology == 1


def test_collection_status_is_fail_closed_and_never_exposes_credentials(tmp_path: Path) -> None:
    settings = replace(
        settings_for(tmp_path),
        shopee_app_id="123456",
        shopee_secret="top-secret",
    )

    status = collection_status(settings)

    assert status["shopee"]["status"] == "BLOCKED_UNVERIFIED_CREDENTIALS"
    assert status["shopee"]["credentials_present"] is True
    assert status["shopee"]["network_calls"] == 0
    assert status["mercadolivre"]["status"] == "BLOCKED_UNVERIFIED_AFFILIATE_CONTRACT"
    assert status["mercadolivre"]["credentials_present"] is None
    assert status["mercadolivre"]["network_calls"] == 0
    assert "top-secret" not in json.dumps(status)


def test_settings_read_collection_credentials_and_filter_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SHOPEE_APP_ID=123456\n"
        "SHOPEE_SECRET=secret\n"
        "TECH_OFFER_TERMS=Notebook, SSD ,placa de video\n",
        encoding="utf-8",
    )
    for name in ("SHOPEE_APP_ID", "SHOPEE_SECRET", "SHOPEE_APP_SECRET", "TECH_OFFER_TERMS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file=env_file)

    assert settings.shopee_app_id == "123456"
    assert settings.shopee_secret == "secret"
    assert settings.technology_offer_terms == ("notebook", "ssd", "placa de video")


def test_assisted_offer_reaches_worker_from_sqlite_without_reopening_source(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    network_calls: list[str] = []
    telegram = TelegramClient(
        None,
        None,
        dry_run=True,
        opener=lambda *_args, **_kwargs: network_calls.append("telegram"),
    )
    pipeline = Pipeline(db, settings, telegram=telegram, source_adapter="assisted")
    batch = prepare_assisted_offers("shopee", [shopee_record()], terms=("notebook",))
    offer_ids = pipeline.ingest_offers(
        batch.offers,
        content_campaign_id="assisted-shopee",
    )

    generated: list[int] = []

    class ContentPipeline:
        def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
            generated.append(offer_id)
            return {"offer_id": offer_id, "campaign_id": campaign_id, "status": "GENERATED"}

    worker = Worker(
        db,
        settings,
        pipeline,
        ContentPipeline(),
        None,
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=run_database_tick,
    )
    worker._process_content_jobs({"status": "idle"})

    assert generated == offer_ids
    assert db.queue_depths(dry_run=True)["publish_queue_depth"] == 1
    assert run_database_tick(db, pipeline, None, None)["result"]["status"] == "SIMULATED"
    assert db.overview()["publications"] == 0
    assert network_calls == []

    pipeline.ingest_offers(
        batch.offers,
        content_campaign_id="assisted-shopee-second-import",
    )
    assert db.rows("SELECT COUNT(*) AS n FROM content_jobs")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) AS n FROM price_history")[0]["n"] == 1


def test_database_worker_mode_does_not_require_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_for(tmp_path)
    monkeypatch.setenv("WORKER_SOURCE_MODE", "database")
    monkeypatch.delenv("WORKER_SOURCE_PATH", raising=False)

    assert _source_from_env(settings) is None


def test_database_worker_and_assisted_import_remain_blocked_outside_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        settings_for(tmp_path),
        dry_run=False,
        public_base_url="https://offers.example",
    )
    monkeypatch.setenv("WORKER_SOURCE_MODE", "database")
    with pytest.raises(ValueError, match="bloqueado fora de DRY_RUN"):
        _source_from_env(settings)

    db = Database(settings.database_path)
    db.init()
    with pytest.raises(RuntimeError, match="bloqueada fora de DRY_RUN"):
        cli._ingest_assisted(
            Pipeline(db, settings, source_adapter="assisted"),
            settings,
            "shopee",
            [shopee_record()],
            campaign="assisted",
        )
    assert db.rows("SELECT COUNT(*) AS n FROM offers")[0]["n"] == 0


def test_stale_assisted_offer_is_not_queued_by_database_worker(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    record = shopee_record(collected_at="2020-01-01T10:00:00-03:00")
    record["expires_at"] = "2020-01-02T10:00:00-03:00"
    batch = prepare_assisted_offers("shopee", [record], terms=("notebook",))
    offer = batch.offers[0]
    offer_id = db.upsert_offer(
        offer,
        content_campaign_id="stale-assisted",
        content_dry_run=True,
    )

    class ContentPipeline:
        def generate_offer(self, _offer_id: int, _campaign_id: str) -> dict:
            raise AssertionError("P1 nao deve gerar conteudo vencido")

    worker = Worker(
        db,
        settings,
        Pipeline(db, settings, source_adapter="assisted"),
        ContentPipeline(),
        None,
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=run_database_tick,
    )
    worker._process_content_jobs({"status": "idle"})

    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"] == 0
    job = db.rows("SELECT offer_id,status,error_type FROM content_jobs")[0]
    assert job["offer_id"] == offer_id
    assert job["status"] == "FAILED"
    assert job["error_type"] == "ComplianceError"


def test_add_offer_cli_is_assisted_and_never_publishes_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AI_REMOTE_PROVIDER", "none")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app.cli", "add-offer", "--adapter", "mercadolivre",
            "--external-id", "MLB123", "--title", "SSD NVMe 1 TB",
            "--category", "Informática", "--price", "399.90",
            "--source-url", "https://produto.mercadolivre.com.br/MLB-123-ssd-_JM",
            "--affiliate-url", "https://mercadolivre.com/sec/Example",
            "--collected-at", utc_now(),
            "--rating", "4.9", "--sales-count", "5000",
            "--commission-rate", "20", "--coupon", "TECH10",
            "--shipping", "frete gratis", "--stock-status", "IN_STOCK",
        ],
    )

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "QUEUED_FOR_LOCAL_WORKER"
    assert result["accepted"] == 1
    db = Database(database_path)
    assert db.rows("SELECT COUNT(*) AS n FROM offers")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) AS n FROM content_jobs WHERE status='PENDING'")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"] == 0

    network_calls: list[str] = []
    settings = replace(settings_for(tmp_path), database_path=database_path)
    pipeline = Pipeline(
        db,
        settings,
        telegram=TelegramClient(
            None,
            None,
            dry_run=True,
            opener=lambda *_args, **_kwargs: network_calls.append("telegram"),
        ),
        source_adapter="assisted",
    )

    class ContentPipeline:
        def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
            return {"offer_id": offer_id, "campaign_id": campaign_id, "status": "GENERATED"}

    worker = Worker(
        db,
        settings,
        pipeline,
        ContentPipeline(),
        None,
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=run_database_tick,
    )
    worker._process_content_jobs({"status": "idle"})
    delivery = run_database_tick(db, pipeline, None, None)

    assert delivery["result"]["status"] == "SIMULATED"
    assert network_calls == []
    telegram_body = db.rows(
        "SELECT body FROM content_packages WHERE channel='telegram' ORDER BY id LIMIT 1"
    )[0]["body"]
    assert "De R$" not in telegram_body


def test_assisted_import_cli_reads_json_from_stdin_without_source_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "stdin.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AI_REMOTE_PROVIDER", "none")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([shopee_record()])))
    monkeypatch.setattr(
        sys,
        "argv",
        ["app.cli", "assisted-import", "--adapter", "shopee"],
    )

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "QUEUED_FOR_LOCAL_WORKER"
    assert result["accepted"] == 1
    assert result["external_publications"] == 0
    db = Database(database_path)
    assert db.rows("SELECT COUNT(*) AS n FROM offers")[0]["n"] == 1
    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"] == 0
