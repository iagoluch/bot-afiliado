from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.db import Database


def classify(score: float) -> str:
    if score >= 75:
        return "EXCELLENT"
    # Stable-price offers with strong rating, volume, coupon, shipping and
    # stock signals remain eligible without inventing a discount claim.
    if score >= 45:
        return "GOOD"
    if score >= 35:
        return "NORMAL"
    return "REJECT"


MIN_FEEDBACK_CLICKS = 5
MIN_FEEDBACK_IMPRESSIONS = 20
MIN_MONETARY_CONVERSIONS = 2
FEEDBACK_CLICK_PRIOR = 20
FEEDBACK_IMPRESSION_PRIOR = 40
MAX_FEEDBACK_ADJUSTMENT = 8.0
MIN_PRIOR_PRICE_OBSERVATIONS = 2


def verified_history_discount(db: Database, offer_id: int) -> tuple[int, float] | None:
    """Comprova queda contra o menor preço de observações anteriores.

    Duas coletas anteriores em instantes distintos evitam transformar o
    primeiro preço riscado recebido da loja em prova de desconto.
    """
    offer = db.get_offer(offer_id)
    if offer is None:
        raise ValueError(f"oferta inexistente: {offer_id}")
    current = int(offer["current_price_cents"])
    row = db.rows(
        """SELECT COUNT(DISTINCT observed_at) observations, MIN(price_cents) reference_price
        FROM price_history
        WHERE offer_id=? AND observed_at < ?""",
        (offer_id, offer["collected_at"]),
    )[0]
    if int(row["observations"]) < MIN_PRIOR_PRICE_OBSERVATIONS:
        return None
    reference = row["reference_price"]
    if reference is None or current <= 0 or current >= int(reference):
        return None
    reference = int(reference)
    return reference, round((reference - current) / reference * 100, 2)


def verified_offer_data(
    db: Database,
    offer_id: int,
    offer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta uma visão efêmera com prova de desconto derivada do banco."""
    stored = db.get_offer(offer_id)
    if stored is None:
        raise ValueError(f"oferta inexistente: {offer_id}")
    data = dict(offer or {})
    data.update(dict(stored))
    try:
        metadata = json.loads(str(data.get("tracking_metadata_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.pop("discount_verification", None)
    discount = verified_history_discount(db, offer_id)
    if discount:
        metadata["discount_verification"] = {
            "method": "PRICE_HISTORY",
            "reference_price_cents": discount[0],
        }
    data["tracking_metadata"] = metadata
    return data


def _performance_snapshot(
    db: Database,
    click_predicate: str,
    click_parameters: tuple[Any, ...],
    impression_predicate: str,
    impression_parameters: tuple[Any, ...],
) -> dict[str, int]:
    row = db.rows(
        f"""WITH selected_clicks AS (
            SELECT c.click_id
            FROM clicks c JOIN offers o ON o.id=c.offer_id
            WHERE {click_predicate}
        ), approved_conversions AS (
            SELECT v.id, v.value_cents, v.commission_cents
            FROM conversions v JOIN selected_clicks c ON c.click_id=v.click_id
            WHERE v.status IN ('APPROVED','PAID')
        ), selected_impressions AS (
            SELECT i.id
            FROM impressions i JOIN offers o ON o.id=i.offer_id
            WHERE i.verified_real=1 AND {impression_predicate}
        )
        SELECT
            (SELECT COUNT(*) FROM selected_clicks) clicks,
            (SELECT COUNT(*) FROM selected_impressions) impressions,
            (SELECT COUNT(*) FROM approved_conversions) conversions,
            (SELECT COALESCE(SUM(value_cents),0) FROM approved_conversions) revenue_cents,
            (SELECT COALESCE(SUM(commission_cents),0) FROM approved_conversions) commission_cents""",
        click_parameters + impression_parameters,
    )[0]
    return {key: int(row[key]) for key in row.keys()}


def _smoothed_rate(numerator: int, denominator: int, baseline: float, prior: int) -> float:
    return (numerator + prior * baseline) / (denominator + prior)


def _bounded_lift(value: float, baseline: float, *, scale: float = 1.0) -> float:
    reference = max(abs(baseline), scale)
    return math.tanh((value - baseline) / reference) * MAX_FEEDBACK_ADJUSTMENT


def _snapshot_adjustment(segment: Mapping[str, int], baseline: Mapping[str, int]) -> float:
    points = 0.0

    if (
        segment["impressions"] >= MIN_FEEDBACK_IMPRESSIONS
        and baseline["impressions"] >= MIN_FEEDBACK_IMPRESSIONS
    ):
        baseline_ctr = baseline["clicks"] / baseline["impressions"]
        smoothed_ctr = _smoothed_rate(
            segment["clicks"],
            segment["impressions"],
            baseline_ctr,
            FEEDBACK_IMPRESSION_PRIOR,
        )
        points += 0.20 * (smoothed_ctr - baseline_ctr) * MAX_FEEDBACK_ADJUSTMENT

    if segment["clicks"] >= MIN_FEEDBACK_CLICKS and baseline["clicks"] >= MIN_FEEDBACK_CLICKS:
        baseline_cvr = baseline["conversions"] / baseline["clicks"]
        smoothed_cvr = _smoothed_rate(
            segment["conversions"],
            segment["clicks"],
            baseline_cvr,
            FEEDBACK_CLICK_PRIOR,
        )
        points += 0.30 * (smoothed_cvr - baseline_cvr) * MAX_FEEDBACK_ADJUSTMENT

        if (
            segment["conversions"] >= MIN_MONETARY_CONVERSIONS
            and baseline["conversions"] >= MIN_MONETARY_CONVERSIONS
        ):
            baseline_epc = baseline["commission_cents"] / baseline["clicks"]
            smoothed_epc = _smoothed_rate(
                segment["commission_cents"],
                segment["clicks"],
                baseline_epc,
                FEEDBACK_CLICK_PRIOR,
            )
            points += 0.30 * _bounded_lift(smoothed_epc, baseline_epc, scale=100.0)

            baseline_rpc = baseline["revenue_cents"] / baseline["clicks"]
            smoothed_rpc = _smoothed_rate(
                segment["revenue_cents"],
                segment["clicks"],
                baseline_rpc,
                FEEDBACK_CLICK_PRIOR,
            )
            points += 0.20 * _bounded_lift(smoothed_rpc, baseline_rpc, scale=100.0)

    return max(-MAX_FEEDBACK_ADJUSTMENT, min(MAX_FEEDBACK_ADJUSTMENT, points))


def performance_adjustment(db: Database, offer_id: int, channel: str, *, at: datetime | None = None) -> float:
    offer = db.get_offer(offer_id)
    if offer is None:
        raise ValueError(f"oferta inexistente: {offer_id}")
    category = str(offer["category"] or "")
    merchant = str(offer["merchant"])
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    hour = moment.astimezone(timezone.utc).strftime("%H")

    cache: dict[tuple[Any, ...], dict[str, int]] = {}

    def snapshot(
        click_predicate: str,
        click_parameters: tuple[Any, ...],
        impression_predicate: str,
        impression_parameters: tuple[Any, ...],
    ) -> dict[str, int]:
        key = (click_predicate, click_parameters, impression_predicate, impression_parameters)
        if key not in cache:
            cache[key] = _performance_snapshot(
                db,
                click_predicate,
                click_parameters,
                impression_predicate,
                impression_parameters,
            )
        return cache[key]

    channel_hour = snapshot(
        "c.channel=? AND strftime('%H',c.timestamp)=?",
        (channel, hour),
        "i.channel=? AND strftime('%H',i.timestamp)=?",
        (channel, hour),
    )
    all_channels_hour = snapshot(
        "strftime('%H',c.timestamp)=?",
        (hour,),
        "strftime('%H',i.timestamp)=?",
        (hour,),
    )
    channel_all_hours = snapshot(
        "c.channel=?",
        (channel,),
        "i.channel=?",
        (channel,),
    )

    scopes: list[tuple[float, dict[str, int], dict[str, int]]] = [
        (
            0.30,
            snapshot("c.offer_id=?", (offer_id,), "i.offer_id=?", (offer_id,)),
            channel_hour,
        ),
        (
            0.20,
            snapshot(
                "c.merchant=? COLLATE NOCASE AND c.channel=? AND strftime('%H',c.timestamp)=?",
                (merchant, channel, hour),
                "i.merchant=? COLLATE NOCASE AND i.channel=? AND strftime('%H',i.timestamp)=?",
                (merchant, channel, hour),
            ),
            channel_hour,
        ),
        (0.125, channel_hour, all_channels_hour),
        (0.125, channel_hour, channel_all_hours),
    ]
    if category:
        scopes.insert(
            1,
            (
                0.25,
                snapshot(
                    "o.category=? COLLATE NOCASE AND c.channel=? AND strftime('%H',c.timestamp)=?",
                    (category, channel, hour),
                    "COALESCE(NULLIF(i.category,''),o.category)=? COLLATE NOCASE AND i.channel=? AND strftime('%H',i.timestamp)=?",
                    (category, channel, hour),
                ),
                channel_hour,
            ),
        )

    adjustment = sum(weight * _snapshot_adjustment(segment, baseline) for weight, segment, baseline in scopes)
    return round(max(-MAX_FEEDBACK_ADJUSTMENT, min(MAX_FEEDBACK_ADJUSTMENT, adjustment)), 2)


def score_offer(db: Database, offer_id: int, *, channel: str = "telegram", at: datetime | None = None) -> tuple[float, str]:
    offer = db.get_offer(offer_id)
    if offer is None:
        raise ValueError(f"oferta inexistente: {offer_id}")
    if offer["stock_status"] == "OUT_OF_STOCK":
        db.set_score(offer_id, 0, "REJECT")
        return 0.0, "REJECT"

    history_discount = verified_history_discount(db, offer_id)
    discount_percent = min(history_discount[1], 80.0) if history_discount else 0.0
    discount_points = min(35.0, discount_percent * 0.7)

    rating = float(offer["rating"] or 0)
    rating_points = max(0.0, min(10.0, (rating - 3.0) * 5.0))
    sales = max(0, int(offer["sales_count"] or 0))
    popularity_points = min(10.0, math.log10(sales + 1) * 3.0)
    commission_points = min(10.0, max(0.0, float(offer["commission_rate"] or 0)) * 0.5)
    coupon_points = 8.0 if offer["coupon"] else 0.0
    shipping_points = 5.0 if offer["shipping"] and "gratis" in offer["shipping"].lower() else 0.0
    stock_points = 10.0 if offer["stock_status"] == "IN_STOCK" else 4.0

    feedback_points = performance_adjustment(db, offer_id, channel, at=at)
    fatigue_penalty = min(20.0, db.publication_count(offer_id) * 5.0)

    score = round(max(0.0, min(100.0, discount_points + rating_points + popularity_points + commission_points + coupon_points + shipping_points + stock_points + feedback_points - fatigue_penalty)), 2)
    classification = classify(score)
    db.set_score(offer_id, score, classification)
    return score, classification
