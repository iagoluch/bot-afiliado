from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

try:
    import fcntl
except ImportError:  # pragma: no cover - available on the Lubuntu target, absent on Windows.
    fcntl = None

from app.config import Settings
from app.services.compliance import validate_distribution, validate_offer
from app.services.site import offer_slug
from app.services.social_content import build_content_specs


class LLMProvider(Protocol):
    def suggest_hook(self, title: str, category: str, channel: str) -> str: ...


class TemplateProvider:
    def suggest_hook(self, title: str, category: str, channel: str) -> str:
        return {
            "telegram": "Confira os detalhes desta oferta",
            "instagram_feed": "Veja os detalhes deste produto",
            "instagram_story": "Conheça este produto",
            "instagram_reel": "Veja este produto em destaque",
            "tiktok": "Olha este produto",
            "site": "Confira os detalhes do produto",
        }[channel]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


_LOCAL_HTTP_OPENER = build_opener(ProxyHandler({}), _NoRedirect())
_HEAVY_WORK_LOCK = threading.Lock()


def _local_urlopen(request: Request, *, timeout: float):
    return _LOCAL_HTTP_OPENER.open(request, timeout=timeout)


@contextmanager
def heavy_work_slot():
    """Serialize Ollama/FFmpeg work in-process and, on Linux, across user processes."""
    with _HEAVY_WORK_LOCK:
        user_id = os.getuid() if hasattr(os, "getuid") else "windows"
        lock_path = Path(tempfile.gettempdir()) / f"bot-afiliado-heavy-work-{user_id}.lock"
        with lock_path.open("a+b") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class OllamaProvider:
    """Local-only Ollama client with a single in-flight inference per process."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3.5:2b",
        *,
        timeout_seconds: int = 300,
        context_length: int = 1024,
        temperature: float = 0.3,
        keep_alive: str = "2m",
        output_limit: int = 2048,
    ):
        parsed = urlsplit(base_url.rstrip("/"))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("OLLAMA_BASE_URL deve apontar para um endereco HTTP loopback")
        if not model.strip():
            raise ValueError("OLLAMA_MODEL nao pode ser vazio")
        if timeout_seconds <= 0 or context_length <= 0 or output_limit <= 0:
            raise ValueError("limites do Ollama devem ser positivos")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperatura do Ollama fora do intervalo permitido")
        if not keep_alive.strip():
            raise ValueError("keep_alive do Ollama nao pode ser vazio")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.context_length = context_length
        self.temperature = temperature
        self.keep_alive = keep_alive.strip()
        self.output_limit = output_limit

    def health(self) -> bool:
        """Return whether Ollama is reachable and the configured model is installed."""
        request = Request(f"{self.base_url}/api/tags", method="GET")
        probe_timeout = min(float(self.timeout_seconds), 3.0)
        for attempt in range(2):
            try:
                with _local_urlopen(request, timeout=probe_timeout) as response:
                    raw = response.read(65_537)
                if len(raw) > 65_536:
                    return False
                payload = json.loads(raw.decode("utf-8"))
                models = payload.get("models") if isinstance(payload, dict) else None
                if not isinstance(models, list):
                    return False
                return any(
                    isinstance(item, dict)
                    and (item.get("name") == self.model or item.get("model") == self.model)
                    for item in models
                )
            except (OSError, TimeoutError):
                if attempt == 0:
                    time.sleep(0.25)
            except (UnicodeDecodeError, ValueError):
                return False
        return False

    def suggest_hook(self, title: str, category: str, channel: str) -> str:
        del title, category  # Untrusted marketplace text must not enter the prompt.
        safe_channel = channel if channel in {
            "telegram", "instagram_feed", "instagram_story", "instagram_reel", "tiktok", "site"
        } else "canal editorial"
        prompt = (
            "Escreva somente uma frase curta em portugues para abrir um rascunho editorial. "
            "Use apenas uma chamada generica no imperativo, sem afirmar qualquer caracteristica do produto. "
            "Nao use numeros, precos, percentuais, cupons, links, estoque, frete, urgencia, comparacoes, "
            "qualidade, beneficio, hashtags ou emojis. Uma linha, sem aspas. "
            f"Canal: {safe_channel}. Frase:"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "keep_alive": self.keep_alive,
                "options": {
                    "num_ctx": self.context_length,
                    "temperature": self.temperature,
                    "num_predict": 64,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with heavy_work_slot():
            with _local_urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("resposta do Ollama excedeu o limite")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("resposta invalida do Ollama") from exc
        text = result.get("response") if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("resposta invalida do Ollama")
        if len(text) > self.output_limit:
            raise ValueError("saida do Ollama excedeu o limite")
        return text.strip()


class LlamaCppProvider:
    """Optional local inference. This class never downloads a model or opens a network service."""

    def __init__(self, cli_path: str | Path, model_path: str | Path, *, timeout_seconds: int = 45):
        self.cli_path = Path(cli_path)
        self.model_path = Path(model_path)
        self.timeout_seconds = timeout_seconds
        if not self.cli_path.is_file() or not self.model_path.is_file():
            raise ValueError("llama.cpp requer executavel e modelo locais existentes")

    def suggest_hook(self, title: str, category: str, channel: str) -> str:
        title = title[:180].replace("\n", " ").replace("\r", " ")
        category = category[:80].replace("\n", " ").replace("\r", " ")
        prompt = (
            "Escreva somente uma frase curta em portugues para abrir um rascunho editorial. "
            "Nao declare preco, desconto, estoque, prazo, frete, beneficio, urgencia ou qualidade. "
            "Nao use numeros, links ou hashtag. Uma linha, sem aspas.\n"
            f"Canal: {channel}. Categoria: {category}. Produto: {title}.\nFrase:"
        )
        command = [
            str(self.cli_path), "-m", str(self.model_path), "-p", prompt,
            "-n", "64", "-c", "1024", "-t", "2", "-ngl", "0",
            "--offline", "--simple-io", "--no-display-prompt", "--no-show-timings",
            "--log-disable", "--single-turn",
        ]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_") and key != "HF_TOKEN"}
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=self.timeout_seconds, check=False, shell=False, env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError("llama.cpp nao concluiu a geracao local")
        if len(completed.stdout) > 2048:
            raise ValueError("saida do llama.cpp excedeu o limite")
        return completed.stdout.strip()


_UNVERIFIED_CLAIM = re.compile(
    r"[\d%$]|https?://|www\.|\b(?:pre[cç]o|desconto|off|estoque|esgot|unidade|frete|"
    r"entrega|cupom|gr[aá]tis|imperd[ií]vel|[uú]ltim|hoje|agora|garantid|"
    r"melhor|benef[ií]cio|qualidade|promo[cç][aã]o|econom|barat|exclusiv|"
    r"lan[cç]amento|novo|novidade|aut[eê]ntic|original|oficial)\w*\b",
    re.IGNORECASE,
)

_SAFE_HOOK_OPENINGS = {"confira", "veja", "conheça", "descubra", "explore", "olha", "saiba"}
_SAFE_HOOK_WORDS = _SAFE_HOOK_OPENINGS | {
    "a", "as", "da", "desta", "deste", "detalhes", "do", "em", "essa", "esse", "esta",
    "este", "item", "mais", "na", "no", "o", "oferta", "opção", "os", "produto", "sobre",
    "um", "uma", "destaque",
}


def validated_hook(value: str) -> str | None:
    """Accept only generic imperative hooks; reject all factual vocabulary."""
    if not isinstance(value, str):
        return None
    hook = unicodedata.normalize("NFC", value).strip().strip('"\'').strip()
    if not hook or len(hook) > 140 or "\n" in hook or "\r" in hook:
        return None
    if _UNVERIFIED_CLAIM.search(hook):
        return None
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ\s,.!?]+", hook):
        return None
    words = re.findall(r"[^\W\d_]+", hook.casefold(), flags=re.UNICODE)
    if not words or words[0] not in _SAFE_HOOK_OPENINGS or any(word not in _SAFE_HOOK_WORDS for word in words):
        return None
    return hook


_safe_hook = validated_hook


def _provider_source(provider: LLMProvider) -> str:
    if isinstance(provider, OllamaProvider):
        return "ollama"
    if isinstance(provider, LlamaCppProvider):
        return "llama.cpp"
    if isinstance(provider, TemplateProvider):
        return "template"
    return "local"


def _log_generation(
    provider: LLMProvider,
    *,
    started_at: float,
    status: str,
    fallback_reason: str | None,
    error_type: str | None = None,
) -> None:
    event = {
        "event": "local_generation",
        "provider": _provider_source(provider),
        "status": status,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
        "fallback_reason": fallback_reason,
        "error_type": error_type,
    }
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":")), file=sys.stderr)


def provider_from_env(settings: Settings | None = None) -> LLMProvider:
    settings = settings or Settings.from_env()
    try:
        ollama = OllamaProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            context_length=settings.ollama_context_length,
            temperature=settings.ollama_temperature,
            keep_alive=settings.ollama_keep_alive,
        )
        if ollama.health():
            return ollama
    except ValueError:
        pass
    cli_path = os.getenv("LLAMA_CLI_PATH", "").strip()
    model_path = os.getenv("LLAMA_MODEL_PATH", "").strip()
    if cli_path and model_path:
        try:
            return LlamaCppProvider(cli_path, model_path)
        except ValueError:
            pass
    return TemplateProvider()


def safe_hook(
    offer: dict, channel: str, *, provider: LLMProvider | None = None
) -> tuple[str, str, str | None]:
    """Generate a non-factual hook and fail closed to a deterministic template."""
    template = TemplateProvider()
    selected = provider or provider_from_env()
    if isinstance(selected, TemplateProvider):
        return (
            template.suggest_hook(str(offer.get("title") or ""), str(offer.get("category") or ""), channel),
            "template",
            "LOCAL_MODEL_NOT_CONFIGURED_OR_MISSING",
        )
    started_at = time.monotonic()
    try:
        hook = validated_hook(
            selected.suggest_hook(
                str(offer.get("title") or ""), str(offer.get("category") or ""), channel
            )
        )
    except Exception as exc:
        hook = None
        fallback_reason = "LOCAL_GENERATION_FAILED"
        _log_generation(
            selected,
            started_at=started_at,
            status="FAILED",
            fallback_reason=fallback_reason,
            error_type=type(exc).__name__,
        )
    else:
        fallback_reason = None if hook is not None else "SUGGESTION_REJECTED"
        _log_generation(
            selected,
            started_at=started_at,
            status="ACCEPTED" if hook is not None else "REJECTED",
            fallback_reason=fallback_reason,
        )
    if hook is not None:
        return hook, _provider_source(selected), None
    return (
        template.suggest_hook(str(offer.get("title") or ""), str(offer.get("category") or ""), channel),
        "template",
        fallback_reason,
    )


def copy_preview(
    offer: dict, channel: str, settings: Settings, *, provider: LLMProvider | None = None
) -> dict:
    """Return a review-only draft; never writes content, scores or publish queues."""
    validate_offer(offer)
    target = {
        "telegram": ("telegram", "text"),
        "instagram_feed": ("instagram", "feed"),
        "instagram_story": ("instagram", "story"),
        "instagram_reel": ("instagram", "reel"),
        "tiktok": ("tiktok", "vertical_video"),
        "site": ("site", "offer_page"),
    }.get(channel)
    if target is None:
        raise ValueError(f"canal de preview desconhecido: {channel}")
    validate_distribution(offer, target[0], settings)
    destination = f"{settings.public_base_url}/o/{offer_slug(offer)}"
    spec = next(
        item for item in build_content_specs(offer, destination, telegram_url=destination)
        if (item.channel, item.format) == target
    )
    hook, source, fallback_reason = safe_hook(offer, channel, provider=provider)
    return {
        "status": "REVIEW_ONLY",
        "source": source,
        "fallback_reason": fallback_reason,
        "channel": channel,
        "hook_suggestion": hook,
        "canonical_caption": spec.caption,
        "hashtags": list(spec.hashtags),
        "script": list(spec.script),
        "note": "Sugestao nao verificada: revisar editorialmente. Nao altera pacotes, filas ou publicacao.",
    }
