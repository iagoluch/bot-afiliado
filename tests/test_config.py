from __future__ import annotations

import os
from pathlib import Path

import pytest
import uvicorn

from app.config import PROJECT_ENV_FILE, Settings, load_project_env, validate_public_base_url
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
        "CLOUDFLARE_ACCOUNT_ID=0123456789abcdef0123456789abcdef\n"
        "CLOUDFLARE_API_TOKEN=secret\n"
        "CLOUDFLARE_AI_MODEL=@cf/google/gemma-4-26b-a4b-it\n"
        "AI_REMOTE_TIMEOUT_SECONDS=9\n"
        "AI_LOCAL_ENABLED=false\n",
        encoding="utf-8",
    )
    for name in (
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_AUTH_TOKEN",
        "CLOUDFLARE_AI_MODEL",
        "AI_REMOTE_TIMEOUT_SECONDS",
        "AI_LOCAL_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(env_file=env_file)

    assert settings.cloudflare_account_id == "0123456789abcdef0123456789abcdef"
    assert settings.cloudflare_api_token == "secret"
    assert settings.cloudflare_ai_model == "@cf/google/gemma-4-26b-a4b-it"
    assert settings.ai_remote_timeout_seconds == 9
    assert settings.ai_local_enabled is False


@pytest.mark.parametrize(
    ("url", "message"),
    (
        ("http://offers.example", "HTTPS"),
        ("https://127.0.0.1", "publicamente acessivel"),
        ("https://localhost", "publicamente acessivel"),
        ("https://offers.example/path", "caminho"),
        ("https://offers.example?token=x", "query"),
        ("https://user:pass@offers.example", "credenciais"),
    ),
)
def test_real_runtime_rejects_unsafe_public_base_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_public_base_url(url, dry_run=False)

    assert validate_public_base_url("https://offers.example/", dry_run=False) == "https://offers.example"
    assert validate_public_base_url("http://127.0.0.1:8000/", dry_run=True) == "http://127.0.0.1:8000"


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
        assert "UMask=0077" in template
        assert "NoNewPrivileges=true" in template


def test_linux_installer_always_restricts_env_permissions() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "install.sh").read_text(encoding="utf-8")

    create_guard = 'if [[ ! -f "$REPO_DIR/.env" ]]; then'
    chmod_line = 'chmod 600 "$REPO_DIR/.env"'

    assert create_guard in script
    assert chmod_line in script
    assert script.index(chmod_line) > script.index("fi", script.index(create_guard))


def test_systemd_installer_requires_runtime_before_enabling_services() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "install-systemd.sh").read_text(encoding="utf-8")

    venv_check = '[[ ! -x "$REPO_DIR/.venv/bin/python" ]]'
    env_check = '[[ ! -f "$REPO_DIR/.env" ]]'
    enable_command = "systemctl enable --now"

    assert venv_check in script
    assert env_check in script
    assert enable_command in script
    assert script.index(venv_check) < script.index(enable_command)
    assert script.index(env_check) < script.index(enable_command)


def test_web_entrypoint_uses_host_and_port_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("WEB_HOST", "127.0.0.9")
    monkeypatch.setenv("WEB_PORT", "8765")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

    web_main()

    assert calls == [{"app": "app.web:app", "host": "127.0.0.9", "port": 8765}]
