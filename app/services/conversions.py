from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from app.db import Database
from app.models import money_to_cents, utc_now


VALID_STATUSES = {"PENDING", "APPROVED", "REJECTED", "PAID"}
MAX_CONVERSION_FILE_BYTES = 50 * 1024 * 1024
MAX_CONVERSION_ROWS = 100_000
MAX_CONVERSION_FIELD_CHARS = 64 * 1024
_REQUIRED_HEADERS = {"external_order_id", "status"}


def _conversion_rows(path: Path) -> Iterator[dict]:
    size = path.stat().st_size
    if size > MAX_CONVERSION_FILE_BYTES:
        raise ValueError(
            f"arquivo de conversoes excede limite de {MAX_CONVERSION_FILE_BYTES} bytes"
        )

    previous_limit = csv.field_size_limit()
    csv.field_size_limit(MAX_CONVERSION_FIELD_CHARS)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)):
                raise ValueError("CSV de conversoes contem cabecalhos duplicados")
            missing = sorted(_REQUIRED_HEADERS - set(headers))
            if missing:
                raise ValueError(
                    "CSV de conversoes sem colunas obrigatorias: " + ", ".join(missing)
                )

            for index, row in enumerate(reader, start=1):
                if index > MAX_CONVERSION_ROWS:
                    raise ValueError(
                        f"CSV de conversoes excede limite de {MAX_CONVERSION_ROWS} linhas"
                    )
                status = str(row.get("status") or "").strip().upper()
                if status not in VALID_STATUSES:
                    raise ValueError(f"status de conversao invalido na linha {index}: {status}")
                offer_id = int(row["offer_id"]) if str(row.get("offer_id") or "").strip() else None
                click_id = str(row.get("click_id") or "").strip() or None
                merchant = str(row.get("merchant") or "").strip() or "Shopee"
                network = str(row.get("network") or "").strip() or "Shopee Afiliados"
                timestamp = str(row.get("timestamp") or "").strip() or utc_now()
                yield {
                    "external_order_id": str(row.get("external_order_id") or "").strip(),
                    "offer_id": offer_id,
                    "click_id": click_id,
                    "merchant": merchant,
                    "network": network,
                    "value_cents": money_to_cents(row.get("value")) or 0,
                    "commission_cents": money_to_cents(row.get("commission")) or 0,
                    "status": status,
                    "channel": str(row.get("channel") or "").strip() or None,
                    "campaign": str(row.get("campaign") or "").strip() or None,
                    "timestamp": timestamp,
                }
    except csv.Error as exc:
        raise ValueError("CSV de conversoes invalido") from exc
    finally:
        csv.field_size_limit(previous_limit)


def import_conversion_csv(db: Database, path: Path) -> int:
    return db.import_conversions(_conversion_rows(path))
