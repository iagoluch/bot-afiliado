from __future__ import annotations

import os
from pathlib import Path

import pytest
import uvicorn

from app.config import PROJECT_ENV_FILE, Settings, load_project_env
from app.web_server import main as web_main


def test_project_env_file_is_anchored_to_repository_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    assert PROJECT_ENV_FILE == Path(__file__).resolve().parents[1] / ".env"


def test_project_env_loads_without_shell_and_preserves_process_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    marker = tmp_path / "shell-was-executed"
    env_file.write_text(
        "\n# configuracao local\n"
        "OLLAMA_TIMEOUT_SECONDS=300\n"
        "DRY_RUN=true\n"
        "VALUE_WITH_EQUALS=a=b=c\n"
        f"LITERAL_SHELL=$(touch {marker})\n"
        "INVALID LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "450")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("VALUE_WITH_EQUALS", raising=False)
    monkeypatch.delenv("LITERAL_SHELL", raising=False)

    loaded = load_project_env(env_file)

    assert loaded == {
        "DRY_RUN": "true",
        "VALUE_WITH_EQUALS": "a=b=c",
        "LITERAL_SHELL": f"$(touch {marker})",
    }
    assert not marker.exists()
    assert os.environ["OLLAMA_TIMEOUT_SECONDS"] == "450"
    assert Settings.from_env(env_file=env_file).ollama_timeout_seconds == 450


def test_settings_reads_explicit_env_file_when_process_value_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_TIMEOUT_SECONDS=300\nOLLAMA_MODEL=qwen3.5:2b\n", encoding="utf-8")
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    settings = Settings.from_env(env_file=env_file)

    assert settings.ollama_timeout_seconds == 300
    assert settings.ollama_model == "qwen3.5:2b"


def test_linux_entrypoints_delegate_env_loading_to_python() -> None:
    root = Path(__file__).resolve().parents[1]

    assert ".env" not in (root / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert ".env" not in (root / "scripts" / "status.sh").read_text(encoding="utf-8")
    for unit in ("bot-afiliado-worker.service.in", "bot-afiliado-web.service.in"):
        template = (root / "scripts" / "systemd" / unit).read_text(encoding="utf-8")
        assert "EnvironmentFile=" not in template


def test_web_entrypoint_uses_host_and_port_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("WEB_HOST", "127.0.0.9")
    monkeypatch.setenv("WEB_PORT", "8765")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

    web_main()

    assert calls == [{"app": "app.web:app", "host": "127.0.0.9", "port": 8765}]
