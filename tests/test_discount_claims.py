from __future__ import annotations

from app.db import Database
from app.models import Offer
from app.services.content import telegram_message, verified_discount
from app.services.curation import score_offer, verified_history_discount, verified_offer_data


def _offer(price: int, collected_at: str) -> Offer:
    return Offer(
        merchant="Loja",
        affiliate_network="Rede",
        external_product_id="produto-1",
        title="Produto",
        current_price_cents=price,
        original_price_cents=99900,
        discount_percent=90,
        source_url="https://loja.example/produto",
        affiliate_url="https://afiliado.example/produto",
        collected_at=collected_at,
        stock_status="UNKNOWN",
    )


def test_telegram_ignores_canonical_discount_without_verification() -> None:
    offer = {
        "title": "Produto",
        "current_price_cents": 8000,
        "original_price_cents": 20000,
        "discount_percent": 60,
        "tracking_metadata": {},
    }

    body = telegram_message(offer, "https://example.test/go/1")

    assert "🔥 OFERTA" in body
    assert "BAIXOU" not in body
    assert "De R$" not in body
    assert "% OFF" not in body


def test_telegram_recalculates_explicit_price_history_verification() -> None:
    offer = {
        "title": "Produto",
        "current_price_cents": 8000,
        "original_price_cents": 99900,
        "discount_percent": 99,
        "tracking_metadata": {
            "discount_verification": {
                "method": "PRICE_HISTORY",
                "reference_price_cents": 10000,
            }
        },
    }

    assert verified_discount(offer) == (10000, 20.0)
    body = telegram_message(offer, "https://example.test/go/1")
    assert "🔥 BAIXOU!" in body
    assert "De R$ 100,00" in body
    assert "20% OFF" in body
    assert "99% OFF" not in body


def test_curation_uses_longitudinal_history_instead_of_claimed_discount(tmp_path) -> None:
    db = Database(tmp_path / "discount.db")
    db.init()

    offer_id = db.upsert_offer(_offer(12000, "2026-09-28T12:00:00+00:00"))
    first_score = score_offer(db, offer_id)[0]
    assert first_score == 4.0
    assert verified_history_discount(db, offer_id) is None

    db.upsert_offer(_offer(11000, "2026-09-29T12:00:00+00:00"))
    assert verified_history_discount(db, offer_id) is None

    db.upsert_offer(_offer(8000, "2026-09-30T12:00:00+00:00"))
    assert verified_history_discount(db, offer_id) == (11000, 27.27)
    assert score_offer(db, offer_id)[0] > first_score

    injected = dict(db.get_offer(offer_id))
    injected["current_price_cents"] = 1
    injected["tracking_metadata"] = {
        "discount_verification": {"method": "PRICE_HISTORY", "reference_price_cents": 999999}
    }
    verified = verified_offer_data(db, offer_id, injected)
    assert verified["current_price_cents"] == 8000
    assert verified_discount(verified) == (11000, 27.27)
