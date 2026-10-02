from __future__ import annotations

from typing import Any

from app.db import Database


DIMENSIONS = ("product", "category", "merchant", "channel", "campaign", "format", "hour", "day", "creative")

_EXPRESSIONS = {
    "clicks": {
        "product": "o.title",
        "category": "COALESCE(NULLIF(o.category,''),'sem categoria')",
        "merchant": "c.merchant",
        "channel": "c.channel",
        "campaign": "c.campaign_id",
        "format": "c.format",
        "hour": "strftime('%H', c.timestamp)",
        "day": "strftime('%Y-%m-%d', c.timestamp)",
        "creative": "c.creative_id",
    },
    "conversions": {
        "product": "COALESCE(o.title,'sem produto')",
        "category": "COALESCE(NULLIF(o.category,''),'sem categoria')",
        "merchant": "v.merchant",
        "channel": "COALESCE(c.channel,v.channel,'unknown')",
        "campaign": "COALESCE(c.campaign_id,v.campaign,'unknown')",
        "format": "COALESCE(c.format,'unknown')",
        # Tempo de conversao pertence ao evento de conversao. O clique continua
        # sendo usado apenas para atribuicao de canal/campanha/criativo.
        "hour": "strftime('%H', v.timestamp)",
        "day": "strftime('%Y-%m-%d', v.timestamp)",
        "creative": "COALESCE(c.creative_id,'unknown')",
    },
    "publications": {
        "product": "o.title",
        "category": "COALESCE(NULLIF(o.category,''),'sem categoria')",
        "merchant": "o.merchant",
        "channel": "p.channel",
        "campaign": "p.campaign_id",
        "format": "cp.format",
        "hour": "strftime('%H', p.published_at)",
        "day": "strftime('%Y-%m-%d', p.published_at)",
        "creative": "p.creative_id",
    },
    "impressions": {
        "product": "o.title",
        "category": "COALESCE(NULLIF(i.category,''),NULLIF(o.category,''),'sem categoria')",
        "merchant": "i.merchant",
        "channel": "i.channel",
        "campaign": "i.campaign_id",
        "format": "i.format",
        "hour": "strftime('%H', i.timestamp)",
        "day": "strftime('%Y-%m-%d', i.timestamp)",
        "creative": "i.creative_id",
    },
}


def _grouped(db: Database, source: str, dimension: str) -> list[dict[str, Any]]:
    expression = _EXPRESSIONS[source][dimension]
    if source == "clicks":
        sql = f"SELECT {expression} segment, COUNT(*) clicks FROM clicks c JOIN offers o ON o.id=c.offer_id GROUP BY segment"
    elif source == "conversions":
        sql = f"""SELECT {expression} segment, COUNT(*) conversions,
            SUM(CASE WHEN v.click_id IS NOT NULL THEN 1 ELSE 0 END) attributed_conversions,
            COALESCE(SUM(v.value_cents),0) revenue_cents,
            COALESCE(SUM(v.commission_cents),0) commission_cents,
            COALESCE(SUM(CASE WHEN v.click_id IS NOT NULL THEN v.commission_cents ELSE 0 END),0)
                attributed_commission_cents
            FROM conversions v LEFT JOIN clicks c ON c.click_id=v.click_id
            LEFT JOIN offers o ON o.id=COALESCE(v.offer_id,c.offer_id)
            WHERE v.status IN ('APPROVED','PAID') GROUP BY segment"""
    elif source == "publications":
        sql = f"""SELECT {expression} segment, COUNT(*) publications
            FROM publications p JOIN offers o ON o.id=p.offer_id
            JOIN publish_queue q ON q.id=p.queue_id
            JOIN content_packages cp ON cp.id=q.content_package_id
            WHERE p.dry_run=0 GROUP BY segment"""
    else:
        sql = f"""SELECT {expression} segment, COUNT(*) impressions
            FROM impressions i JOIN offers o ON o.id=i.offer_id
            WHERE i.verified_real=1 GROUP BY segment"""
    return [dict(row) for row in db.rows(sql)]


def analytics_breakdown(db: Database, dimension: str) -> list[dict[str, Any]]:
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimensao invalida: {dimension}")
    merged: dict[str, dict[str, Any]] = {}
    for source in ("clicks", "conversions", "publications", "impressions"):
        for row in _grouped(db, source, dimension):
            segment = str(row.pop("segment") or "unknown")
            merged.setdefault(segment, {
                "segment": segment,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0,
                "attributed_conversions": 0,
                "publications": 0,
                "revenue_cents": 0,
                "commission_cents": 0,
                "attributed_commission_cents": 0,
            }).update(row)
    results: list[dict[str, Any]] = []
    for item in merged.values():
        clicks = int(item["clicks"])
        impressions = int(item["impressions"])
        attributed_conversions = int(item["attributed_conversions"])
        publications = int(item["publications"])
        attributed_commission = int(item["attributed_commission_cents"])
        commission = int(item["commission_cents"])
        revenue = int(item["revenue_cents"])
        item.update({
            "ctr": round(clicks / impressions * 100, 2) if impressions else None,
            "cvr": round(attributed_conversions / clicks * 100, 2) if clicks else None,
            "epc_cents": round(attributed_commission / clicks, 2) if clicks else None,
            "revenue_per_post_cents": round(revenue / publications, 2) if publications else None,
            "commission_per_post_cents": round(commission / publications, 2) if publications else None,
        })
        results.append(item)
    return sorted(results, key=lambda row: (-row["commission_cents"], -row["clicks"], row["segment"]))
