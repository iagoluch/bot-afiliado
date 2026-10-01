from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


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
    channel_visibility: dict[str, str] | None = None
    merchant_channel_rules: dict[str, dict] | None = None
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None
    instagram_graph_api_version: str | None = None
    instagram_media_base_url: str | None = None
    instagram_asset_allowed_hosts: tuple[str, ...] = ()
    instagram_container_api_enabled: bool = False
    instagram_facebook_login_ready: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:2b"
    ollama_timeout_seconds: int = 120
    ollama_context_length: int = 1024
    ollama_temperature: float = 0.3
    ollama_keep_alive: str = "2m"
    ollama_think: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
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
            channel_visibility={
                "site": "public",
                "telegram": "public",
                "instagram": "public",
                "tiktok": "public",
                **{str(key).lower(): str(value).lower() for key, value in _json_object_env("CHANNEL_VISIBILITY_JSON").items()},
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
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:2b").strip(),
            ollama_timeout_seconds=_int_env("OLLAMA_TIMEOUT_SECONDS", 120),
            ollama_context_length=_int_env("OLLAMA_CONTEXT_LENGTH", 1024),
            ollama_temperature=_float_env("OLLAMA_TEMPERATURE", 0.3, minimum=0.0, maximum=2.0),
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "2m").strip(),
            ollama_think=_bool_env("OLLAMA_THINK", False),
        )
        if settings.ollama_think:
            raise ValueError("OLLAMA_THINK deve permanecer false neste hardware")
        if not settings.ollama_model:
            raise ValueError("OLLAMA_MODEL nao pode ser vazio")
        if not settings.ollama_keep_alive:
            raise ValueError("OLLAMA_KEEP_ALIVE nao pode ser vazio")
        return settings
