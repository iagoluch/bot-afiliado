from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


MAX_LOCAL_FEED_BYTES = 50 * 1024 * 1024
MAX_LOCAL_FEED_ROWS = 50_000
MAX_LOCAL_FIELD_CHARS = 64 * 1024


def read_local_records(path: Path, *, label: str) -> list[dict[str, Any]]:
    if path.stat().st_size > MAX_LOCAL_FEED_BYTES:
        raise ValueError(f"arquivo {label} excede limite de {MAX_LOCAL_FEED_BYTES} bytes")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_LOCAL_FIELD_CHARS)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or []
                if len(headers) != len(set(headers)):
                    raise ValueError(f"arquivo {label} contem cabecalhos duplicados")
                records: list[dict[str, Any]] = []
                for row_number, row in enumerate(reader, start=2):
                    if len(records) >= MAX_LOCAL_FEED_ROWS:
                        raise ValueError(
                            f"arquivo {label} excede limite de {MAX_LOCAL_FEED_ROWS} linhas"
                        )
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"linha {row_number}: estrutura CSV invalida")
                    records.append(dict(row))
                return records
        except csv.Error as exc:
            raise ValueError(f"CSV {label} invalido") from exc
        finally:
            csv.field_size_limit(previous_limit)

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON {label} invalido") from exc
        records = data if isinstance(data, list) else data.get("offers", []) if isinstance(data, dict) else []
        if not isinstance(records, list):
            raise ValueError(f"JSON {label} deve conter uma lista de ofertas")
        if len(records) > MAX_LOCAL_FEED_ROWS:
            raise ValueError(f"arquivo {label} excede limite de {MAX_LOCAL_FEED_ROWS} linhas")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError(f"JSON {label} contem oferta invalida")
        return [dict(record) for record in records]

    raise ValueError(f"formato {label} aceito: .csv ou .json")
