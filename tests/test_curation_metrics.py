from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import Database
from app.models import Offer
from app.services.curation import MAX_FEEDBACK_ADJUSTMENT, performance_adjustment, score_offer


AT_10 = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)
TS_10 = "2026-01-01T10:15:00+00:00"
TS_09 = "2026-01-01T09:15:00+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "curation.db")
    database.init()
    return database


def add_offer(db: Database, external_id: str, *, merchant: str, category: str) -> int:
    return db.upsert_offer(Offer(
        merchant=merchant,
        affiliate_network="Test Network",
        external_product_id=external_id,
        title=f"Produto {external_id}",
        category=category,
        current_price_cents=10_000,
        source_url=f"https://merchant.example/{external_id}",
        affiliate_url=f"https://affiliate.example/{external_id}",
        stock_status="IN_STOCK",
    ))


def add_history(
    db: Database,
    offer_id: int,
    *,
    label: str,
    clicks: int,
    impressions: int = 0,
    channel: str = "telegram",
    timestamp: str = TS_10,
    approved: list[tuple[int, int]] | None = None,
) -> list[str]:
    offer = db.get_offer(offer_id)
    assert offer is not None
    click_ids = [f"{label}-click-{index}" for index in range(clicks)]
    with db.connect() as connection:
        for click_id in click_ids:
            connection.execute(
                """INSERT INTO clicks(
                    click_id,offer_id,merchant,channel,campaign_id,creative_id,format,timestamp
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (click_id, offer_id, offer["merchant"], channel, "feedback", label, "text", timestamp),
            )
        for index in range(impressions):
            connection.execute(
                """INSERT INTO impressions(
                    offer_id,merchant,category,channel,campaign_id,format,creative_id,
                    timestamp,source,verified_real
                ) VALUES(?,?,?,?,?,?,?,?,?,1)""",
                (
                    offer_id,
                    offer["merchant"],
                    offer["category"],
                    channel,
                    "feedback",
                    "text",
                    label,
                    timestamp,
                    "official-export",
                ),
            )
    for index, (revenue_cents, commission_cents) in enumerate(approved or []):
        add_conversion(
            db,
            order=f"{label}-approved-{index}",
            offer_id=offer_id,
            click_id=click_ids[index],
            status="APPROVED",
            revenue_cents=revenue_cents,
            commission_cents=commission_cents,
            channel=channel,
            timestamp=timestamp,
        )
    return click_ids


def add_conversion(
    db: Database,
    *,
    order: str,
    offer_id: int,
    click_id: str,
    status: str,
    revenue_cents: int,
    commission_cents: int,
    channel: str = "telegram",
    timestamp: str = TS_10,
) -> None:
    db.import_conversion({
        "external_order_id": order,
        "offer_id": offer_id,
        "click_id": click_id,
        "merchant": db.get_offer(offer_id)["merchant"],
        "network": "Test Network",
        "value_cents": revenue_cents,
        "commission_cents": commission_cents,
        "status": status,
        "channel": channel,
        "campaign": "feedback",
        "timestamp": timestamp,
    })


def test_one_conversion_without_sample_volume_does_not_change_priority(db: Database) -> None:
    offer_id = add_offer(db, "one-event", merchant="Loja A", category="Casa")
    click_id = add_history(db, offer_id, label="one", clicks=1)[0]
    add_conversion(
        db,
        order="one-large-order",
        offer_id=offer_id,
        click_id=click_id,
        status="APPROVED",
        revenue_cents=1_000_000,
        commission_cents=500_000,
    )

    assert performance_adjustment(db, offer_id, "telegram", at=AT_10) == 0.0


def test_epc_and_revenue_use_only_approved_or_paid_conversions(db: Database) -> None:
    high_id = add_offer(db, "high", merchant="Loja Alta", category="Casa")
    low_id = add_offer(db, "low", merchant="Loja Baixa", category="Casa")
    add_history(
        db,
        high_id,
        label="high",
        clicks=10,
        impressions=100,
        approved=[(20_000, 2_000), (20_000, 2_000)],
    )
    low_clicks = add_history(
        db,
        low_id,
        label="low",
        clicks=10,
        impressions=100,
        approved=[(2_000, 200), (2_000, 200)],
    )

    high_before = performance_adjustment(db, high_id, "telegram", at=AT_10)
    low_before = performance_adjustment(db, low_id, "telegram", at=AT_10)
    assert high_before > low_before

    add_conversion(
        db,
        order="low-rejected",
        offer_id=low_id,
        click_id=low_clicks[2],
        status="REJECTED",
        revenue_cents=9_000_000,
        commission_cents=9_000_000,
    )
    add_conversion(
        db,
        order="low-pending",
        offer_id=low_id,
        click_id=low_clicks[3],
        status="PENDING",
        revenue_cents=9_000_000,
        commission_cents=9_000_000,
    )

    assert performance_adjustment(db, high_id, "telegram", at=AT_10) == high_before
    assert performance_adjustment(db, low_id, "telegram", at=AT_10) == low_before
    assert -MAX_FEEDBACK_ADJUSTMENT <= low_before < high_before <= MAX_FEEDBACK_ADJUSTMENT


def test_new_offer_inherits_bounded_store_channel_and_hour_performance(db: Database) -> None:
    good_history = add_offer(db, "good-history", merchant="Loja Boa", category="Geral")
    bad_history = add_offer(db, "bad-history", merchant="Loja Ruim", category="Geral")
    other_channel = add_offer(db, "other-channel", merchant="Outra", category="Outra")
    old_hour = add_offer(db, "old-hour", merchant="Outra 2", category="Outra")
    add_history(
        db,
        good_history,
        label="good-history",
        clicks=10,
        impressions=60,
        approved=[(10_000, 1_000)] * 4,
    )
    add_history(db, bad_history, label="bad-history", clicks=10, impressions=100)
    add_history(
        db,
        other_channel,
        label="other-channel",
        clicks=10,
        impressions=100,
        channel="email",
    )
    add_history(db, old_hour, label="old-hour", clicks=10, impressions=100, timestamp=TS_09)

    good_new = add_offer(db, "good-new", merchant="Loja Boa", category="Geral")
    bad_new = add_offer(db, "bad-new", merchant="Loja Ruim", category="Geral")
    neutral_new = add_offer(db, "neutral-new", merchant="Sem Historico", category="Nova")

    good = performance_adjustment(db, good_new, "telegram", at=AT_10)
    bad = performance_adjustment(db, bad_new, "telegram", at=AT_10)
    neutral = performance_adjustment(db, neutral_new, "telegram", at=AT_10)
    neutral_old_hour = performance_adjustment(
        db,
        neutral_new,
        "telegram",
        at=datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc),
    )

    assert good > neutral > bad
    assert neutral > neutral_old_hour
    assert all(-MAX_FEEDBACK_ADJUSTMENT <= value <= MAX_FEEDBACK_ADJUSTMENT for value in (good, bad, neutral))


def test_raw_click_volume_without_outcome_or_impressions_is_not_a_bonus(db: Database) -> None:
    clicked_id = add_offer(db, "clicked", merchant="Loja A", category="Casa")
    untouched_id = add_offer(db, "untouched", merchant="Loja B", category="Casa")
    add_history(db, clicked_id, label="clicked", clicks=100)

    assert score_offer(db, clicked_id, at=AT_10) == score_offer(db, untouched_id, at=AT_10)


def test_strong_stable_price_offer_is_good_without_unverified_discount(db: Database) -> None:
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="stable-strong",
        title="Fone Bluetooth",
        current_price_cents=11_990,
        original_price_cents=None,
        discount_percent=None,
        coupon="OFERTA10",
        shipping="Frete gratis",
        rating=4.8,
        sales_count=3_500,
        commission_rate=12,
        source_url="https://shopee.com.br/product/1/stable-strong",
        affiliate_url="https://s.shopee.com.br/stable-strong",
        stock_status="IN_STOCK",
    ))
    score, classification = score_offer(db, offer_id)
    assert score == 48
    assert classification == "GOOD"
