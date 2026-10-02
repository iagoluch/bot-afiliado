from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import Database


def is_in_cooldown(
    db: Database,
    offer_id: int,
    channel: str,
    campaign_id: str | None = None,
    *,
    dry_run: bool = False,
    product_hours: int = 24,
    category_minutes: int = 30,
    campaign_hours: int = 24,
) -> bool:
    now = datetime.now(timezone.utc)
    product_cutoff = (now - timedelta(hours=product_hours)).isoformat(timespec="seconds")
    if db.rows(
        "SELECT 1 FROM publications WHERE offer_id=? AND channel=? AND dry_run=0 AND published_at>=? LIMIT 1",
        (offer_id, channel, product_cutoff),
    ):
        return True
    offer = db.get_offer(offer_id)
    if offer is None:
        return True
    if campaign_id:
        campaign_cutoff = (now - timedelta(hours=campaign_hours)).isoformat(timespec="seconds")
        if db.rows(
            "SELECT 1 FROM publications WHERE offer_id=? AND channel=? AND campaign_id=? AND dry_run=0 AND published_at>=? LIMIT 1",
            (offer_id, channel, campaign_id, campaign_cutoff),
        ):
            return True
    category = str(offer["category"] or "").strip()
    if category:
        category_cutoff = (now - timedelta(minutes=category_minutes)).isoformat(timespec="seconds")
        if db.rows(
            "SELECT 1 FROM publications p JOIN offers o ON o.id=p.offer_id WHERE p.channel=? AND p.dry_run=0 AND o.category=? AND p.published_at>=? LIMIT 1",
            (channel, category, category_cutoff),
        ):
            return True
        if db.rows(
            "SELECT 1 FROM publish_queue q JOIN offers o ON o.id=q.offer_id WHERE q.channel=? AND q.dry_run=? AND q.status IN ('PENDING','PROCESSING','FAILED') AND o.category=? LIMIT 1",
            (channel, int(dry_run), category),
        ):
            return True
    return False
