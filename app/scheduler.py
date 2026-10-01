from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.db import Database
from app.services.pipeline import Pipeline


DEFAULT_SLOTS = ("07:00", "10:00", "13:00", "16:00", "19:00", "22:00")


def due_slot(now: datetime, slots: tuple[str, ...] = DEFAULT_SLOTS, tolerance_minutes: int = 5) -> str | None:
    for slot in slots:
        hour, minute = (int(part) for part in slot.split(":"))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_minutes = (now - scheduled).total_seconds() / 60
        if 0 <= delta_minutes < tolerance_minutes:
            return f"{now.date().isoformat()}T{slot}"
    return None


def run_due(db: Database, pipeline: Pipeline, source: Path, now: datetime) -> dict | None:
    slot = due_slot(now)
    mode = "dry" if pipeline.settings.dry_run else "real"
    source_adapter = getattr(pipeline, "source_adapter", "shopee")
    slot_key = f"{slot}:{mode}:{source_adapter}" if slot else None
    if slot_key is None or not db.claim_schedule_slot(slot_key):
        return None
    try:
        return pipeline.run(source, campaign_id=f"scheduled-{slot}")
    except Exception:
        db.release_schedule_slot(slot_key)
        raise


def run_tick(db: Database, pipeline: Pipeline, source: Path, now: datetime) -> dict:
    """Executa ingestao na janela e trabalha a fila em toda invocacao.

    `pipeline.run` ja processa a fila depois da ingestao. Fora da janela, ou
    quando a janela deste modo ja foi consumida, processamos um item vencido
    sem reler a fonte.
    """
    cycle = run_due(db, pipeline, source, now)
    if cycle is not None:
        return {"status": "cycle", "result": cycle}
    queue_result = pipeline.process_one()
    if queue_result is not None:
        return {"status": "queue", "result": queue_result}
    return {"status": "idle"}
