from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.cli import main
from app.config import Settings
from app.db import Database
from app.services.site import offer_slug
from app.web import create_app


def test_admitad_cli_imports_locally_without_queue_or_publication(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "publisher-export.csv"
    source.write_text(
        "article,name,price,currencyID,url\n"
        "sku-1,Cafeteira,99.90,BRL,https://ad.admitad.com/g/abc123/?subid=canal-1\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "affiliate.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(
        sys,
        "argv",
        ["app.cli", "import-offers", str(source), "--adapter", "admitad", "--merchant-name", "Loja aprovada"],
    )

    main()

    result = json.loads(capsys.readouterr().out)
    db = Database(database_path)
    assert result["status"] == "PENDING_MERCHANT_REVIEW"
    assert len(result["offers"]) == 1
    assert db.get_offer(result["offers"][0])["affiliate_url"].endswith("?subid=canal-1")
    assert db.rows("SELECT COUNT(*) AS total FROM publish_queue")[0]["total"] == 0
    assert db.rows("SELECT COUNT(*) AS total FROM social_queue")[0]["total"] == 0

    client = TestClient(create_app(Settings.from_env(), db))
    offer = db.get_offer(result["offers"][0])
    assert "Cafeteira" not in client.get("/offers").text
    assert client.get(f"/o/{offer_slug(offer)}").status_code == 403
    assert client.get(f"/go/{offer['id']}").status_code == 403
    assert db.rows("SELECT COUNT(*) AS total FROM clicks")[0]["total"] == 0
