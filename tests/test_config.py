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
        "AI_REMOTE_TIMEOUT_SECONDS=12\n"
        "DRY_RUN=true\n"
        "VALUE_WITH_EQUALS=a=b=c\n"
        f"LITERAL_SHELL=$(touch {marker})\n"
        "INVALID LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_REMOTE_TIMEOUT_SECONDS", "18")
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
    assert os.environ["AI_REMOTE_TIMEOUT_SECONDS"] == "18"
    assert Settings.from_env(env_file=env_file).ai_remote_timeout_seconds == 18


def test_settings_reads_ai_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=secret\n"
        "GEMINI_MODEL=gemini-3.8-flash\n"
        "AI_REMOTE_TIMEOUT_SECONDS=9\n"
        "AI_LOCAL_ENABLED=false\n",
        encoding="utf-8",
    )
    for name in ("GEMINI_API_KEY","GOOGLE_API_KEY","GEMINI_MODEL","AI_REMOTE_TIMEOUT_SECONDS","AI_LOCAL_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file=env_file)

    assert settings.gemini_api_key == "secret"
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.ai_remote_timeout_seconds == 9
    assert settings.ai_local_enabled is False


def test_local_ai_requires_explicit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AI_LOCAL_ENABLED=true\n", encoding="utf-8")
    for name in ("AI_LOCAL_ENABLED","GRANITE_CLI_PATH","GRANITE_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="GRANITE_CLI_PATH"):
        Settings.from_env(env_file=env_file)


def test_linux_entrypoints_delegate_env_loading_to_python() -> None:
    root = Path(__file__).resolve().parents[1]

    assert ".env" not in (root / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert ".env" not in (root / "scripts" / "status.sh").read_text(encoding="utf-8")
    for unit in ("bot-afiliado-worker.service.in", "bot-afiliado-web.service.in"):
        template = (root / "scripts" / "systemd" / unit).read_text(encoding="utf-8")
        assert "EnvironmentFile=" not in template


def test_web_entrypoint_uses_host_and_port_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("WEB_HOST", "127.0.0.9")
    monkeypatch.setenv("WEB_PORT", "8765")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

    web_main()

    assert calls == [{"app": "app.web:app", "host": "127.0.0.9", "port": 8765}]
