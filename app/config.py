from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(env_file: Path | str = PROJECT_ENV_FILE) -> dict[str, str]:
    """Load literal KEY=VALUE pairs without executing shell syntax.

    Existing process variables win over the project file. The returned mapping
    contains only values added to os.environ by this call.
    """
    path = Path(env_file)
    if not path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not _ENV_KEY.fullmatch(key):
            continue
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} deve ser true ou false; recebido: {value!r}")


def _json_object_env(name: str) -> dict:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} deve ser JSON valido") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} deve ser um objeto JSON")
    return value


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser um numero inteiro") from exc
    if value < minimum:
        raise ValueError(f"{name} deve ser maior ou igual a {minimum}")
    return value


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser um numero") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} deve estar entre {minimum} e {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    database_path: Path
    dry_run: bool
    public_base_url: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    creatives_path: Path = Path("data/creatives")
    ffmpeg_path: str | None = None
    amazon_creators_client_id: str | None = None
    amazon_creators_client_secret: str | None = None
    amazon_partner_tag_br: str | None = None
    awin_allowed_affiliate_hosts: tuple[str, ...] = ()
    awin_allowed_image_hosts: tuple[str, ...] = ()
    admin_password: str | None = None
    web_bind_host: str = "127.0.0.1"
    web_bind_port: int = 8000
    channel_visibility: dict[str, str] | None = None
    merchant_channel_rules: dict[str, dict] | None = None
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None
    instagram_graph_api_version: str | None = None
    instagram_media_base_url: str | None = None
    instagram_asset_allowed_hosts: tuple[str, ...] = ()
    instagram_container_api_enabled: bool = False
    instagram_facebook_login_ready: bool = False
    ai_remote_provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    ai_remote_timeout_seconds: int = 12
    ai_local_enabled: bool = False
    granite_cli_path: str | None = None
    granite_model_path: str | None = None
    ai_local_timeout_seconds: int = 20
    ai_circuit_failures: int = 2
    ai_circuit_cooldown_seconds: int = 300

    @classmethod
    def from_env(cls, *, env_file: Path | str = PROJECT_ENV_FILE) -> "Settings":
        load_project_env(env_file)
        settings = cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/affiliate.db")),
            dry_run=_bool_env("DRY_RUN", True),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            creatives_path=Path(os.getenv("CREATIVES_PATH", "data/creatives")),
            ffmpeg_path=os.getenv("FFMPEG_PATH") or None,
            amazon_creators_client_id=os.getenv("AMAZON_CREATORS_CLIENT_ID") or None,
            amazon_creators_client_secret=os.getenv("AMAZON_CREATORS_CLIENT_SECRET") or None,
            amazon_partner_tag_br=os.getenv("AMAZON_PARTNER_TAG_BR") or None,
            awin_allowed_affiliate_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("AWIN_ALLOWED_AFFILIATE_HOSTS", "").split(",")
                if host.strip()
            ),
            awin_allowed_image_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("AWIN_ALLOWED_IMAGE_HOSTS", "").split(",")
                if host.strip()
            ),
            admin_password=os.getenv("ADMIN_PASSWORD") or None,
            web_bind_host=os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
            web_bind_port=_int_env("WEB_PORT", 8000),
            channel_visibility={
                "site": "public",
                "telegram": "public",
                "instagram": "public",
                "tiktok": "public",
                **{
                    str(key).lower(): str(value).lower()
                    for key, value in _json_object_env("CHANNEL_VISIBILITY_JSON").items()
                },
            },
            merchant_channel_rules={
                str(key).lower(): value
                for key, value in _json_object_env("MERCHANT_CHANNEL_RULES_JSON").items()
                if isinstance(value, dict)
            },
            instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN") or None,
            instagram_user_id=os.getenv("INSTAGRAM_USER_ID") or None,
            instagram_graph_api_version=os.getenv("INSTAGRAM_GRAPH_API_VERSION") or None,
            instagram_media_base_url=os.getenv("INSTAGRAM_MEDIA_BASE_URL") or None,
            instagram_asset_allowed_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("INSTAGRAM_ASSET_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            ),
            instagram_container_api_enabled=_bool_env("INSTAGRAM_REEL_CONTAINER_API_ENABLED", False),
            instagram_facebook_login_ready=_bool_env("INSTAGRAM_FACEBOOK_LOGIN_READY", False),
            ai_remote_provider=os.getenv("AI_REMOTE_PROVIDER", "gemini").strip().lower() or "gemini",
            gemini_api_key=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
            ai_remote_timeout_seconds=_int_env("AI_REMOTE_TIMEOUT_SECONDS", 12),
            ai_local_enabled=_bool_env("AI_LOCAL_ENABLED", False),
            granite_cli_path=os.getenv("GRANITE_CLI_PATH") or None,
            granite_model_path=os.getenv("GRANITE_MODEL_PATH") or None,
            ai_local_timeout_seconds=_int_env("AI_LOCAL_TIMEOUT_SECONDS", 20),
            ai_circuit_failures=_int_env("AI_CIRCUIT_FAILURES", 2),
            ai_circuit_cooldown_seconds=_int_env("AI_CIRCUIT_COOLDOWN_SECONDS", 300),
        )
        if settings.ai_remote_provider not in {"gemini", "none"}:
            raise ValueError("AI_REMOTE_PROVIDER deve ser gemini ou none")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", settings.gemini_model):
            raise ValueError("GEMINI_MODEL invalido")
        if settings.ai_local_enabled and not (settings.granite_cli_path and settings.granite_model_path):
            raise ValueError("AI_LOCAL_ENABLED exige GRANITE_CLI_PATH e GRANITE_MODEL_PATH")
        if settings.web_bind_port > 65535:
            raise ValueError("WEB_PORT deve ser menor ou igual a 65535")
        return settings
