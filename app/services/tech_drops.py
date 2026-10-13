"""Concise Tech Drops editorial renderer using a structured product profile.

No category-specific phrase factories, inferred features or claims of savings.
Payment, price, shipping and stock are handled by the verified offer renderer.
"""
from __future__ import annotations

import hashlib
import re

from app.services.product_profile import ProductProfile, interpret_product


_SELLER_CODE = re.compile(r"\b(?:100-\d{7,}[A-Z0-9]*|MLB-?\d{8,})\b", re.I)
_UNTRUSTED_MARKUP = re.compile(r"[\x00-\x1f\x7f]+")
_GENERIC = re.compile(
    r"(?:mais uma op[cç][aã]o|op[cç][aã]o de tecnologia|"
    r"estilo [uú]nico|produto imperd[ií]vel|confira esta oferta|"
    r"pensando em trocar (?:de )?celular)",
    re.I,
)
# Keep optional AI as a non-factual invitation, not a source of facts.
_UNVERIFIED_CLAIM = re.compile(
    r"\b(?:tem|possui|oferece|inclui|garante|melhor|perfeito|"
    r"premium|excelente|resistente|imperme[aá]vel|original|"
    r"aut[eê]ntico|certificado|ultrar[r]?[aá]pido)\b",
    re.I,
)


def clean_title(raw: str, max_length: int = 106) -> str:
    value = " ".join(_SELLER_CODE.sub("", _UNTRUSTED_MARKUP.sub(" ", raw)).split())
    value = value.strip(" -|,;")
    if len(value) > max_length:
        value = value[:max_length + 1].rsplit(" ", 1)[0].strip(" -|,;") + "…"
    return value or "Produto de tecnologia"


def _choice(identity: str, salt: str) -> int:
    return hashlib.sha256((salt + ":" + identity).encode("utf-8")).digest()[0] % 2


def _valid_creative_hook(profile: ProductProfile, hook: str | None) -> bool:
    if not hook or profile.confidence != "TITLE_MATCH":
        return False
    if _GENERIC.search(hook) or _UNVERIFIED_CLAIM.search(hook):
        return False
    # Product compatibility is never enough. "iPhone" inside a charger
    # ad must not validate a copy referring to buying a smartphone.
    return any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", hook, re.I)
        for alias in profile.category.aliases
    )


def creative_copy(title: str, identity: str, ai_hook: str | None = None) -> tuple[str, str]:
    """Render stable, grounded, non-repetitive copy using the same profile."""
    profile = interpret_product(title)
    category = profile.category
    name = category.label.upper()
    if category.code == "unknown":
        heading = "🔎 TECH DROP NA ÁREA!"
    elif _choice(identity, "headline"):
        heading = f"{category.emoji} {name} NA SELEÇÃO!"
    else:
        heading = f"{category.emoji} {name} NO RADAR!"

    if _valid_creative_hook(profile, ai_hook):
        return heading, ai_hook or ""

    # Literal facts only. A no-specification title should not produce a
    # generic "Confira essa opção" paragraph pretending to be creativity.
    if not profile.features:
        return heading, ""
    facts = " • ".join(profile.features)
    descriptions = (
        f"{category.label.capitalize()} com {facts} informados no anúncio.",
        f"Nos detalhes deste {category.label}: {facts}.",
        f"Na seleção de {category.label}: {facts} em destaque na descrição.",
    )
    slot = hashlib.sha256(("body:" + identity).encode("utf-8")).digest()[0] % len(descriptions)
    return heading, descriptions[slot]
