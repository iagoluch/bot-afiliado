from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.content import brl, verified_discount
from app.services.content import telegram_message


@dataclass(frozen=True, slots=True)
class ContentSpec:
    channel: str
    format: str
    caption: str
    script: tuple[dict[str, Any], ...]
    hashtags: tuple[str, ...]
    link_url: str | None = None

    def payload(self, *, destination_url: str) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "format": self.format,
            "caption": self.caption,
            "script": list(self.script),
            "hashtags": list(self.hashtags),
            "destination_url": self.link_url or destination_url,
            "disclosure": "#publi",
        }


def _facts(offer: dict[str, Any]) -> list[str]:
    facts = [f"Por {brl(offer['current_price_cents'])}"]
    discount = verified_discount(offer)
    if discount:
        facts.insert(0, f"De {brl(discount[0])}")
    if offer.get("coupon"):
        facts.append(f"Cupom: {offer['coupon']}")
    if offer.get("shipping"):
        facts.append(str(offer["shipping"]))
    return facts


def _updated(offer: dict[str, Any]) -> str:
    raw = str(offer.get("updated_at") or offer.get("collected_at") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return raw


def build_content_specs(
    offer: dict[str, Any],
    destination_url: str,
    *,
    telegram_url: str | None = None,
) -> tuple[ContentSpec, ...]:
    title = str(offer["title"])
    facts = _facts(offer)
    price = brl(offer["current_price_cents"])
    verified = verified_discount(offer)
    price_scene = f"De {brl(verified[0])} → por {price}" if verified else f"Por {price}"
    discount = f"{verified[1]:.0f}% de desconto comprovado" if verified else "Confira as condições"
    coupon = f"Cupom {offer['coupon']}" if offer.get("coupon") else "Confira cupons disponíveis"
    hashtags = ("#publi", "#oferta", "#achadinhos")

    feed_caption = "\n".join(("#publi", title, *facts, f"Atualizado em {_updated(offer)}", f"Detalhes: {destination_url}", " ".join(hashtags[1:])))
    story_caption = "\n".join(("#publi", title, price, coupon, "Veja os detalhes antes de comprar", destination_url))
    reel_caption = "\n".join(("#publi", title, *facts, "Preço e disponibilidade podem mudar. Confira no link.", destination_url, "#reels #oferta"))
    tiktok_caption = "\n".join(("#publi", title, price, "Confira preço e condições atuais no link.", destination_url, "#achadinhos #oferta"))

    reel_script = (
        {"start": 0, "end": 2, "role": "hook", "text": title},
        {"start": 2, "end": 6, "role": "product", "text": str(offer.get("description") or offer.get("category") or title)},
        {"start": 6, "end": 10, "role": "price", "text": price_scene},
        {"start": 10, "end": 14, "role": "benefit", "text": f"{discount}. {coupon}."},
        {"start": 14, "end": 18, "role": "cta", "text": "Confira preço e disponibilidade no link"},
    )
    tiktok_script = (
        {"start": 0, "end": 1.5, "role": "hook", "text": f"Olha este achado: {title}"},
        {"start": 1.5, "end": 5, "role": "product", "text": str(offer.get("description") or title)},
        {"start": 5, "end": 9, "role": "price", "text": price},
        {"start": 9, "end": 12, "role": "benefit", "text": coupon},
        {"start": 12, "end": 15, "role": "cta", "text": "Confira as condições atuais no link"},
    )

    return (
        ContentSpec("instagram", "feed", feed_caption, (), hashtags),
        ContentSpec("instagram", "story", story_caption, (), hashtags),
        ContentSpec("instagram", "reel", reel_caption, reel_script, hashtags + ("#reels",)),
        ContentSpec("tiktok", "vertical_video", tiktok_caption, tiktok_script, hashtags + ("#tiktok",)),
        ContentSpec("site", "offer_page", f"#publi\n{title}\n{price}\n{destination_url}", (), ("#publi",)),
        ContentSpec(
            "telegram",
            "text",
            telegram_message(offer, telegram_url or destination_url),
            (),
            ("#publi",),
            telegram_url or destination_url,
        ),
    )
