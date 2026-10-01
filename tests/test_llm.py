from __future__ import annotations

import subprocess
import json
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.cli import main
from app.db import Database
from app.models import Offer
from app.services.compliance import ComplianceError
from app.services.llm import LlamaCppProvider, copy_preview, provider_from_env


def _settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "preview.db", True, "http://127.0.0.1:8000", None, None)


def _offer() -> dict:
    return {
        "id": 7,
        "merchant": "Shopee",
        "affiliate_network": "Shopee Afiliados",
        "title": "Fone sem fio",
        "category": "Eletronicos",
        "current_price_cents": 9000,
        "original_price_cents": 12000,
        "coupon": None,
        "shipping": None,
        "stock_status": "IN_STOCK",
        "source_url": "https://shopee.com.br/product/1/7",
        "affiliate_url": "https://s.shopee.com.br/7",
        "collected_at": "2026-09-30T00:00:00Z",
    }


def test_preview_falls_back_without_binary_or_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LLAMA_CLI_PATH", raising=False)
    monkeypatch.delenv("LLAMA_MODEL_PATH", raising=False)
    assert provider_from_env().__class__.__name__ == "TemplateProvider"
    draft = copy_preview(_offer(), "instagram_feed", _settings(tmp_path))
    assert draft["status"] == "REVIEW_ONLY"
    assert draft["source"] == "template"
    assert draft["fallback_reason"] == "LOCAL_MODEL_NOT_CONFIGURED_OR_MISSING"
    assert "R$ 90,00" in draft["canonical_caption"]
    assert "R$ 120,00" not in draft["canonical_caption"]
    assert "#publi" in draft["canonical_caption"]
    assert not (tmp_path / "preview.db").exists()


def test_llama_uses_local_argv_only_and_never_accepts_new_claims(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cli = tmp_path / "llama-cli.exe"
    model = tmp_path / "small.gguf"
    cli.touch()
    model.touch()
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, "Conheça os detalhes deste produto", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("LLAMA_ARG_MODEL_URL", "https://example.invalid/model.gguf")
    provider = LlamaCppProvider(cli, model)
    draft = copy_preview(_offer(), "instagram_story", _settings(tmp_path), provider=provider)
    assert draft["source"] == "llama.cpp"
    assert draft["hook_suggestion"] == "Conheça os detalhes deste produto"
    assert calls[0]["command"][0] == str(cli)
    assert "--offline" in calls[0]["command"]
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == 45
    assert "LLAMA_ARG_MODEL_URL" not in calls[0]["env"]

    def fabricated_discount(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "Hoje 70% OFF, só 2 em estoque", "")

    monkeypatch.setattr(subprocess, "run", fabricated_discount)
    rejected = copy_preview(_offer(), "instagram_story", _settings(tmp_path), provider=provider)
    assert rejected["source"] == "template"
    assert rejected["fallback_reason"] == "SUGGESTION_REJECTED"
    assert "70%" not in str(rejected)


def test_timeout_and_invalid_offer_fail_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cli = tmp_path / "llama-cli.exe"
    model = tmp_path / "small.gguf"
    cli.touch()
    model.touch()

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    draft = copy_preview(_offer(), "telegram", _settings(tmp_path), provider=LlamaCppProvider(cli, model))
    assert draft["source"] == "template"
    assert draft["fallback_reason"] == "LOCAL_GENERATION_FAILED"
    expired = {**_offer(), "expires_at": "2020-01-01T00:00:00Z"}
    with pytest.raises(ComplianceError):
        copy_preview(expired, "telegram", _settings(tmp_path), provider=LlamaCppProvider(cli, model))
    amazon = {**_offer(), "merchant": "Amazon", "affiliate_network": "Amazon Associados"}
    with pytest.raises(ComplianceError):
        copy_preview(amazon, "telegram", _settings(tmp_path), provider=LlamaCppProvider(cli, model))


def test_cli_preview_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "cli.db"
    db = Database(db_path)
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee", affiliate_network="Shopee Afiliados", external_product_id="llm-1",
        title="Fone sem fio", current_price_cents=9000,
        source_url="https://shopee.com.br/product/1/7",
        affiliate_url="https://s.shopee.com.br/7",
    ))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.delenv("LLAMA_CLI_PATH", raising=False)
    monkeypatch.delenv("LLAMA_MODEL_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["app.cli", "copy-preview", str(offer_id), "--channel", "telegram"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "REVIEW_ONLY"
    assert result["source"] == "template"
    assert db.rows("SELECT COUNT(*) AS n FROM content_packages")[0]["n"] == 0
    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"] == 0
