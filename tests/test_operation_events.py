from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.cli import _run_recorded, main
from app.db import Database


def test_scheduler_events_are_persisted_without_offer_text_or_secrets(tmp_path) -> None:
    db = Database(tmp_path / "operations.db")
    db.init()

    cycle = {"offers": [1], "queue": [2], "publications": [{"status": "SIMULATED"}]}
    assert _run_recorded(db, "dry", "shopee", lambda: cycle) == cycle
    queue = {"status": "queue", "result": {"queue_id": 2, "status": "RATE_LIMITED", "retry_at": "later"}}
    assert _run_recorded(db, "real", "shopee", lambda: queue) == queue
    assert _run_recorded(db, "dry", "shopee", lambda: {"status": "idle"}) == {"status": "idle"}

    def fail() -> dict:
        raise RuntimeError("token-super-secret")

    with pytest.raises(RuntimeError, match="token-super-secret"):
        _run_recorded(db, "real", "shopee", fail)

    events = [dict(row) for row in db.list_operation_events()]
    assert [row["status"] for row in events] == ["FAILED", "RATE_LIMITED", "COMPLETED"]
    assert events[0]["error_type"] == "RuntimeError"
    assert events[1]["queue_id"] == 2
    assert events[2]["offers"] == 1
    assert events[2]["queued"] == 1
    assert events[2]["processed"] == 1
    assert "token-super-secret" not in json.dumps(events)


def test_log_failure_does_not_change_completed_cycle_result(tmp_path, monkeypatch, capsys) -> None:
    db = Database(tmp_path / "operations.db")
    db.init()

    def fail_log(*args, **kwargs) -> None:
        raise OSError("secret-path")

    monkeypatch.setattr(db, "record_operation_event", fail_log)
    cycle = {"offers": [], "queue": [], "publications": []}
    assert _run_recorded(db, "real", "shopee", lambda: cycle) == cycle
    warning = json.loads(capsys.readouterr().err)
    assert warning == {"event": "operation_log_failed", "error_type": "OSError"}


def test_cli_cycle_and_events_work_in_dry_run(tmp_path, monkeypatch, capsys) -> None:
    source = Path(__file__).resolve().parents[1] / "examples" / "shopee_offers.sample.csv"
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "operations.db"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(sys, "argv", ["app.cli", "cycle", str(source), "--campaign", "events-check"])
    main()
    cycle = json.loads(capsys.readouterr().out)
    assert len(cycle["offers"]) == 1
    assert len(cycle["queue"]) == 1
    assert [item["status"] for item in cycle["publications"]] == ["SIMULATED"]

    monkeypatch.setattr(sys, "argv", ["app.cli", "events", "--limit", "1"])
    main()
    events = json.loads(capsys.readouterr().out)
    assert len(events) == 1
    assert events[0]["event"] == "cycle"
    assert events[0]["status"] == "COMPLETED"
    assert events[0]["mode"] == "dry"
    assert events[0]["offers"] == 1
    assert events[0]["queued"] == 1
    assert events[0]["processed"] == 1
