from __future__ import annotations

import re
import unicodedata
from typing import Mapping, Any


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:70] or "oferta"


def offer_slug(offer: Mapping[str, Any]) -> str:
    return f"{slugify(str(offer['title']))}-{int(offer['id'])}"


def offer_id_from_slug(slug: str) -> int | None:
    match = re.fullmatch(r"[a-z0-9-]+-(\d+)", slug)
    return int(match.group(1)) if match else None
