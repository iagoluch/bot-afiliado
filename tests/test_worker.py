from __future__ import annotations

import csv
import json
import sqlite3
import sys
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.adapters.shopee_manual import ShopeeManualAdapter
from app.config import Settings
from app.db import Database, MAX_CONTENT_JOB_ATTEMPTS
from app.scheduler import run_tick
from app.services.llm import TemplateProvider
from app.services.analytics import analytics_breakdown
from app.services.conversions import import_conversion_csv
from app.services.p1 import P1Pipeline
from app.services.pipeline import Pipeline
from app.web import create_app
from app.worker import (
    Worker,
    WorkerAlreadyRunning,
    WorkerLock,
    _positive_float_env,
    _source_from_env,
    retry_sqlite,
    runtime_status,
)


ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "worker.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
    )


class FakePipeline:
    source_adapter = "shopee"


class FakeContentPipeline:
    def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
        return {"offer_id": offer_id, "campaign_id": campaign_id, "status": "GENERATED"}


def worker_for(
    tmp_path: Path,
    tick,
    *,
    content_pipeline=None,
    stop_event: threading.Event | None = None,
) -> tuple[Worker, Database, Settings]:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    worker = Worker(
        db,
        settings,
        FakePipeline(),
        content_pipeline or FakeContentPipeline(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=3,
        error_backoff_seconds=5,
        tick=tick,
        stop_event=stop_event,
    )
    return worker, db, settings


def test_worker_idle_waits_and_stops_gracefully_with_persisted_heartbeat(tmp_path: Path) -> None:
    worker, db, _settings = worker_for(tmp_path, lambda *_args: {"status": "idle"})
    waits: list[float] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return True

    worker._wait = wait
    worker.run_forever()

    assert waits == [3]
    state = dict(db.worker_runtime())
    assert state["status"] == "STOPPED"
    assert state["pid"] is None
    assert state["heartbeat_at"]
    assert state["last_tick_at"]
    assert state["last_result"] == "stopped"


def test_worker_recovers_after_exception_with_progressive_backoff(tmp_path: Path) -> None:
    calls = 0

    def tick(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("falha recuperavel")
        return {"status": "idle"}

    worker, db, _settings = worker_for(tmp_path, tick)
    waits: list[float] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return len(waits) == 2

    worker._wait = wait
    worker.run_forever()

    assert calls == 2
    assert waits == [5, 3]
    assert db.worker_runtime()["status"] == "STOPPED"


def test_operation_log_failure_warns_once_without_raw_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    worker, db, _settings = worker_for(tmp_path, lambda *_args: {"status": "idle"})

    def fail_log(*_args, **_kwargs):
        raise RuntimeError("segredo-nao-pode-aparecer")

    db.record_operation_event = fail_log
    result = {"status": "queue", "result": {"status": "SIMULATED", "queue_id": 1}}
    worker._record_result(result)
    worker._record_result(result)

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    warning = json.loads(lines[0])
    assert warning == {"event": "operation_log_failed", "error_type": "RuntimeError"}
    assert "segredo" not in lines[0]


def test_cycle_generates_content_sequentially_and_isolates_offer_failure(tmp_path: Path) -> None:
    source_offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    cycle = {
        "status": "cycle",
        "result": {
            "campaign_id": "scheduled-2026-10-01T07:00",
            "offers": [],
            "queue": [],
            "publications": [],
        },
    }
    ticks = iter((cycle, {"status": "idle"}))

    class ContentPipeline:
        def __init__(self):
            self.calls: list[int] = []

        def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
            self.calls.append(offer_id)
            if len(self.calls) == 1:
                raise ValueError("oferta invalida")
            return {"offer_id": offer_id, "status": "GENERATED", "campaign_id": campaign_id}

    content = ContentPipeline()
    worker, db, _settings = worker_for(tmp_path, lambda *_args: next(ticks), content_pipeline=content)
    first = db.upsert_offer(
        source_offer, content_campaign_id="scheduled-2026-10-01T07:00", content_dry_run=True,
    )
    second = db.upsert_offer(
        replace(source_offer, external_product_id="worker-second-offer", title="Segundo produto"),
        content_campaign_id="scheduled-2026-10-01T07:00",
        content_dry_run=True,
    )
    cycle["result"]["offers"] = [first, second]
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert content.calls == [first, second]
    assert cycle["result"]["content"] == [
        {"offer_id": first, "status": "FAILED", "error_type": "ValueError"},
        {"offer_id": second, "status": "GENERATED", "campaign_id": "scheduled-2026-10-01T07:00"},
    ]
    jobs = db.rows("SELECT offer_id,status FROM content_jobs ORDER BY id")
    assert [(row["offer_id"], row["status"]) for row in jobs] == [(first, "FAILED"), (second, "COMPLETED")]


def test_content_job_retries_stop_at_bounded_attempt_limit(tmp_path: Path) -> None:
    db = Database(tmp_path / "bounded-content.db")
    db.init()
    offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    db.upsert_offer(offer, content_campaign_id="bounded-retry", content_dry_run=True)

    for _ in range(MAX_CONTENT_JOB_ATTEMPTS):
        job = db.claim_content_job(dry_run=True)
        assert job is not None
        db.fail_content_job(int(job["id"]), "ValueError", "1970-01-01T00:00:00+00:00")

    assert db.claim_content_job(dry_run=True) is None
    row = db.rows("SELECT status,attempts,error_type FROM content_jobs")[0]
    assert row["status"] == "FAILED"
    assert row["attempts"] == MAX_CONTENT_JOB_ATTEMPTS
    assert row["error_type"] == "ValueError"


def test_sqlite_locked_retry_is_bounded_and_uses_backoff() -> None:
    calls = 0
    waits: list[float] = []

    def action() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert retry_sqlite(action, wait=lambda seconds: waits.append(seconds) or False) == "ok"
    assert calls == 3
    assert waits == [0.1, 0.2]

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        retry_sqlite(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")), wait=lambda _seconds: False)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_worker_intervals_reject_non_finite_or_non_positive_values(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_IDLE_SECONDS", value)
    with pytest.raises(ValueError, match="maior que zero"):
        _positive_float_env("WORKER_IDLE_SECONDS", 30)


def test_real_mode_rejects_sample_source_even_when_explicitly_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(settings_for(tmp_path), dry_run=False)
    monkeypatch.setenv("WORKER_SOURCE_PATH", str(ROOT / "examples" / "shopee_offers.sample.csv"))
    monkeypatch.chdir(ROOT)
    with pytest.raises(ValueError, match="sample"):
        _source_from_env(settings)


def test_worker_lock_allows_only_one_owner_and_recovers_after_release(tmp_path: Path) -> None:
    first = WorkerLock(tmp_path / "worker.lock")
    second = WorkerLock(tmp_path / "worker.lock")
    first.acquire()
    try:
        with pytest.raises(WorkerAlreadyRunning):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_runtime_status_and_health_details_expose_only_safe_operational_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        settings_for(tmp_path),
        cloudflare_account_id="0123456789abcdef0123456789abcdef",
        cloudflare_api_token="segredo-nao-expor",
        cloudflare_ai_model="@cf/google/gemma-4-26b-a4b-it",
    )
    db = Database(settings.database_path)
    db.init()
    db.record_worker_heartbeat("RUNNING", pid=123, started_at="2026-10-01T12:00:00+00:00")
    status = runtime_status(db, settings, ffmpeg_probe=lambda _settings: False)

    assert status["database"] == "ok"
    assert status["worker"]["status"] == "running"
    assert status["queue_depth"] == 0
    assert status["ai"] == {
        "remote_provider": "cloudflare",
        "remote_model": "@cf/google/gemma-4-26b-a4b-it",
        "remote_configured": True,
        "local_provider": None,
        "local_enabled": False,
        "local_configured": False,
        "fallback": "template",
    }
    assert status["ffmpeg_available"] is False
    serialized = json.dumps(status)
    assert "segredo-nao-expor" not in serialized
    assert "TELEGRAM" not in serialized
    assert "affiliate_url" not in serialized
    assert "123" not in serialized

    monkeypatch.setattr("app.web.runtime_status", lambda *_args: status)
    client = TestClient(create_app(settings, db))
    assert client.get("/health").json() == {"status": "ok", "dry_run": True}
    detailed = client.get("/health?details=true").json()
    assert detailed["runtime"] == status


def test_runtime_status_cli_uses_safe_status_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    expected = {"database": "ok", "worker": {"status": "stopped"}, "dry_run": True}
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(cli, "runtime_status", lambda *_args: expected)
    monkeypatch.setattr(sys, "argv", ["app.cli", "runtime-status"])

    cli.main()

    assert json.loads(capsys.readouterr().out) == expected


def test_persisted_queue_is_processed_after_database_reopen(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    pipeline = Pipeline(db, settings)
    offer_ids = pipeline.ingest_source(ROOT / "examples" / "shopee_offers.sample.csv", "shopee")
    queue_ids = pipeline.curate_and_queue(offer_ids, "restart-test")
    assert queue_ids

    reopened = Database(settings.database_path)
    reopened.init()
    result = Pipeline(reopened, settings).process_one()

    assert result is not None
    assert result["status"] == "SIMULATED"
    assert reopened.queue_depth(dry_run=True) == 0


def test_content_job_survives_restart_immediately_after_ingestion(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    first_db = Database(settings.database_path)
    first_db.init()
    offers = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")
    offer_ids = Pipeline(first_db, settings).ingest_offers(offers, content_campaign_id="crash-after-ingest")
    assert first_db.rows("SELECT status FROM content_jobs")[0]["status"] == "PENDING"

    reopened = Database(settings.database_path)
    reopened.init()
    generated: list[tuple[int, str]] = []

    class RecoveringContent:
        def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
            generated.append((offer_id, campaign_id))
            return {"offer_id": offer_id, "status": "GENERATED"}

    worker = Worker(
        reopened,
        settings,
        FakePipeline(),
        RecoveringContent(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=lambda *_args: {"status": "idle"},
    )
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert generated == [(offer_ids[0], "crash-after-ingest")]
    assert reopened.rows("SELECT status FROM content_jobs")[0]["status"] == "COMPLETED"


def test_processing_content_job_is_recovered_after_worker_crash(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    offer_id = db.upsert_offer(offer, content_campaign_id="crash-during-p1", content_dry_run=True)
    claimed = db.claim_content_job(dry_run=True)
    assert claimed is not None
    assert claimed["status"] == "PROCESSING"

    reopened = Database(settings.database_path)
    reopened.init()
    calls: list[int] = []

    class RecoveredContent:
        def generate_offer(self, current_offer_id: int, _campaign_id: str) -> dict:
            calls.append(current_offer_id)
            return {"offer_id": current_offer_id, "status": "GENERATED"}

    worker = Worker(
        reopened,
        settings,
        FakePipeline(),
        RecoveredContent(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=lambda *_args: {"status": "idle"},
    )
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert calls == [offer_id]
    job = reopened.rows("SELECT status,attempts FROM content_jobs")[0]
    assert job["status"] == "COMPLETED"
    assert job["attempts"] == 1


def test_new_offer_facts_in_same_campaign_supersede_old_content_job(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    offer_id = db.upsert_offer(offer, content_campaign_id="same-campaign", content_dry_run=True)
    updated_offer = replace(offer, current_price_cents=offer.current_price_cents + 100)
    db.upsert_offer(
        updated_offer,
        content_campaign_id="same-campaign",
        content_dry_run=True,
    )
    db.upsert_offer(updated_offer, content_campaign_id="same-campaign", content_dry_run=True)
    assert len(db.rows("SELECT id FROM content_jobs")) == 2
    generated: list[int] = []

    class CurrentOnlyContent:
        def generate_offer(self, current_offer_id: int, _campaign_id: str) -> dict:
            generated.append(current_offer_id)
            return {"offer_id": current_offer_id, "status": "GENERATED"}

    worker = Worker(
        db,
        settings,
        FakePipeline(),
        CurrentOnlyContent(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=lambda *_args: {"status": "idle"},
    )
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert generated == [offer_id]
    assert [row["status"] for row in db.rows("SELECT status FROM content_jobs ORDER BY id")] == [
        "SUPERSEDED", "COMPLETED",
    ]


def test_same_offer_version_across_schedule_windows_reuses_single_p1_job(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    offer_id = db.upsert_offer(offer, content_campaign_id="scheduled-07:00", content_dry_run=True)
    job = db.claim_content_job(dry_run=True)
    assert job is not None
    db.complete_content_job(job["id"])

    db.upsert_offer(offer, content_campaign_id="scheduled-10:00", content_dry_run=True)
    assert len(db.rows("SELECT id FROM content_jobs")) == 1

    changed = replace(offer, coupon="NOVO-CUPOM")
    db.upsert_offer(changed, content_campaign_id="scheduled-13:00", content_dry_run=True)
    jobs = db.rows("SELECT offer_id,status FROM content_jobs ORDER BY id")
    assert len(jobs) == 2
    assert jobs[0]["offer_id"] == offer_id
    assert jobs[0]["status"] == "COMPLETED"
    assert jobs[1]["status"] == "PENDING"


def test_content_jobs_are_strictly_isolated_between_dry_and_real_modes(tmp_path: Path) -> None:
    dry_settings = settings_for(tmp_path)
    db = Database(dry_settings.database_path)
    db.init()
    offer = ShopeeManualAdapter().import_offers(ROOT / "examples" / "shopee_offers.sample.csv")[0]
    offer_id = db.upsert_offer(offer, content_campaign_id="mode", content_dry_run=True)
    db.upsert_offer(offer, content_campaign_id="mode", content_dry_run=False)
    generated: list[int] = []

    class DryContent:
        def generate_offer(self, current_offer_id: int, _campaign_id: str) -> dict:
            generated.append(current_offer_id)
            return {"offer_id": current_offer_id, "status": "GENERATED"}

    worker = Worker(
        db,
        dry_settings,
        FakePipeline(),
        DryContent(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=lambda *_args: {"status": "idle"},
    )
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert generated == [offer_id]
    jobs = db.rows("SELECT dry_run,status FROM content_jobs ORDER BY dry_run DESC")
    assert [(row["dry_run"], row["status"]) for row in jobs] == [(1, "COMPLETED"), (0, "PENDING")]
    assert db.queue_depth(dry_run=True) == 0
    assert db.queue_depth(dry_run=False) == 1


def test_offline_e2e_worker_reaches_idle_and_restart_preserves_state(tmp_path: Path) -> None:
    settings = replace(
        settings_for(tmp_path),
        ffmpeg_path=str(tmp_path / "ffmpeg-ausente"),
    )
    db = Database(settings.database_path)
    db.init()
    pipeline = Pipeline(db, settings)
    p1 = P1Pipeline(db, settings, provider=TemplateProvider())

    class FastP1:
        def generate_offer(self, offer_id: int, campaign_id: str) -> dict:
            return p1.generate_offer(offer_id, campaign_id, duration_scale=0.01)

    tick_count = 0

    def scheduled_then_idle(database, current_pipeline, source, _now):
        nonlocal tick_count
        tick_count += 1
        scheduled = datetime(2026, 10, 1, 7, 1) if tick_count == 1 else datetime(2026, 10, 1, 8, 0)
        return run_tick(database, current_pipeline, source, scheduled)

    worker = Worker(
        db,
        settings,
        pipeline,
        FastP1(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=scheduled_then_idle,
    )
    worker._wait = lambda _seconds: True
    worker.run_forever()

    assert tick_count == 2
    assert db.rows("SELECT status FROM publish_queue")[0]["status"] == "SIMULATED"
    assert db.rows("SELECT status FROM content_jobs")[0]["status"] == "COMPLETED"
    hook_payload = json.loads(db.rows(
        "SELECT payload_json FROM content_packages WHERE format='reel' LIMIT 1"
    )[0]["payload_json"])["editorial_hook"]
    assert hook_payload == {
        "text": "Veja este produto em destaque",
        "source": "template",
        "fallback_reason": "AI_PROVIDER_NOT_CONFIGURED",
    }
    packages = db.rows("SELECT assets_json FROM content_packages WHERE assets_json!='[]'")
    assert packages
    relative_assets = [item for row in packages for item in json.loads(row["assets_json"])]
    assert relative_assets
    assert all((settings.creatives_path / item).is_file() for item in relative_assets)
    first_runtime = dict(db.worker_runtime())
    assert first_runtime["last_cycle_at"]

    offer_id = int(db.rows("SELECT id FROM offers LIMIT 1")[0]["id"])
    client = TestClient(create_app(settings, db))
    for _ in range(5):
        response = client.get(
            f"/go/{offer_id}",
            params={
                "channel": "telegram",
                "campaign_id": "demo-offline",
                "creative_id": "demo-copy",
                "format": "text",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
    click_ids = [row["click_id"] for row in db.rows("SELECT click_id FROM clicks ORDER BY timestamp LIMIT 2")]
    conversions = tmp_path / "conversions.csv"
    with conversions.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "external_order_id", "click_id", "status", "value", "commission",
        ))
        writer.writeheader()
        for index, click_id in enumerate(click_ids, start=1):
            writer.writerow({
                "external_order_id": f"demo-{index}",
                "click_id": click_id,
                "status": "APPROVED",
                "value": "100.00",
                "commission": "10.00",
            })
    assert import_conversion_csv(db, conversions) == 2
    assert import_conversion_csv(db, conversions) == 2
    metrics = analytics_breakdown(db, "channel")[0]
    assert metrics["segment"] == "telegram"
    assert metrics["clicks"] == 5
    assert metrics["conversions"] == 2
    assert metrics["commission_cents"] == 2000
    assert metrics["cvr"] == 40.0
    assert metrics["epc_cents"] == 400.0

    reopened = Database(settings.database_path)
    reopened.init()
    second = Worker(
        reopened,
        settings,
        Pipeline(reopened, settings),
        FakeContentPipeline(),
        ROOT / "examples" / "shopee_offers.sample.csv",
        idle_seconds=1,
        error_backoff_seconds=1,
        tick=lambda *_args: {"status": "idle"},
    )
    second._wait = lambda _seconds: True
    second.run_forever()

    assert reopened.rows("SELECT status FROM publish_queue")[0]["status"] == "SIMULATED"
    assert reopened.rows("SELECT status FROM content_jobs")[0]["status"] == "COMPLETED"
    assert reopened.overview()["clicks"] == 5
    assert reopened.overview()["conversions"] == 2
    assert reopened.worker_runtime()["heartbeat_at"] >= first_runtime["heartbeat_at"]
