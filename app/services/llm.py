from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

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
    r"[\d%$]|https?://|\b(?:pre[cç]o|desconto|off|estoque|esgot|unidade|frete|"
    r"entrega|cupom|gr[aá]tis|imperd[ií]vel|[uú]ltim|hoje|agora|garantid|"
    r"melhor|benef[ií]cio|qualidade)\b",
    re.IGNORECASE,
)


def _safe_hook(value: str) -> str | None:
    hook = value.strip().strip('"\'').strip()
    if not hook or len(hook) > 140 or "\n" in hook or "\r" in hook:
        return None
    if _UNVERIFIED_CLAIM.search(hook):
        return None
    return hook


def provider_from_env() -> LLMProvider:
    cli_path = os.getenv("LLAMA_CLI_PATH", "").strip()
    model_path = os.getenv("LLAMA_MODEL_PATH", "").strip()
    if cli_path and model_path:
        try:
            return LlamaCppProvider(cli_path, model_path)
        except ValueError:
            pass
    return TemplateProvider()


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
    selected = provider or provider_from_env()
    template = TemplateProvider()
    hook = None
    source = "template"
    fallback_reason = None
    if not isinstance(selected, TemplateProvider):
        try:
            hook = _safe_hook(selected.suggest_hook(str(offer["title"]), str(offer.get("category") or ""), channel))
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            fallback_reason = "LOCAL_GENERATION_FAILED"
        if hook is not None:
            source = "llama.cpp"
        elif fallback_reason is None:
            fallback_reason = "SUGGESTION_REJECTED"
    if hook is None:
        hook = template.suggest_hook(str(offer["title"]), str(offer.get("category") or ""), channel)
        if fallback_reason is None:
            fallback_reason = "LOCAL_MODEL_NOT_CONFIGURED_OR_MISSING"
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
