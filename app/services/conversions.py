from __future__ import annotations

import csv
from pathlib import Path

from app.db import Database
from app.models import money_to_cents, utc_now


VALID_STATUSES = {"PENDING", "APPROVED", "REJECTED", "PAID"}


def import_conversion_csv(db: Database, path: Path) -> int:
    imported = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = str(row.get("status") or "").strip().upper()
            if status not in VALID_STATUSES:
                raise ValueError(f"status de conversao invalido: {status}")
            offer_id = int(row["offer_id"]) if row.get("offer_id") else None
            click_id = str(row.get("click_id") or "").strip() or None
            db.import_conversion({
                "external_order_id": str(row["external_order_id"]).strip(),
                "offer_id": offer_id,
                "click_id": click_id,
                "merchant": str(row.get("merchant") or "Shopee").strip(),
                "network": str(row.get("network") or "Shopee Afiliados").strip(),
                "value_cents": money_to_cents(row.get("value")) or 0,
                "commission_cents": money_to_cents(row.get("commission")) or 0,
                "status": status,
                "channel": str(row.get("channel") or "").strip() or None,
                "campaign": str(row.get("campaign") or "").strip() or None,
                "timestamp": str(row.get("timestamp") or utc_now()),
            })
            imported += 1
    return imported
