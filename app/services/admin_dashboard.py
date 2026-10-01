from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.adapters.amazon_creators import AmazonCreatorsAdapter
from app.adapters.awin_feed import AwinFeedAdapter
from app.adapters.mercadolivre_manual import MercadoLivreManualAdapter
from app.adapters.shopee_manual import ShopeeManualAdapter
from app.config import Settings
from app.db import Database
from app.services.analytics import DIMENSIONS, analytics_breakdown
from app.services.compliance import DEFAULT_MERCHANT_RULES


PAGES = {
    "overview": "Overview",
    "offers": "Offers",
    "queue": "Queue",
    "published": "Published",
    "content": "Content",
    "channels": "Channels",
    "merchants": "Merchants",
    "affiliate-programs": "Affiliate Programs",
    "clicks": "Clicks",
    "conversions": "Conversions",
    "revenue": "Revenue",
    "analytics": "Analytics",
    "compliance": "Compliance",
    "settings": "Settings",
}


def _rows(db: Database, sql: str, parameters: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.rows(sql, parameters)]


def page_data(db: Database, settings: Settings, page: str, *, dimension: str = "channel") -> Any:
    if page == "overview":
        return db.overview()
    if page == "offers":
        return _rows(db, "SELECT id,title,category,merchant,affiliate_network,current_price_cents,stock_status,deal_score,score_class,updated_at FROM offers ORDER BY updated_at DESC LIMIT 200")
    if page == "queue":
        return _rows(db, """SELECT * FROM (SELECT 'telegram' queue_type,q.id,o.title,q.channel,q.status,q.campaign_id,q.attempts,q.last_error,q.updated_at updated_at
            FROM publish_queue q JOIN offers o ON o.id=q.offer_id
            UNION ALL
            SELECT 'social',s.id,o.title,s.channel,s.status,c.campaign_id,0,s.required_action,s.updated_at
            FROM social_queue s JOIN offers o ON o.id=s.offer_id JOIN content_packages c ON c.id=s.content_package_id
            ) combined ORDER BY combined.updated_at DESC LIMIT 200""")
    if page == "published":
        return _rows(db, """SELECT p.id,o.title,p.channel,p.campaign_id,p.creative_id,p.external_message_id,p.published_at
            FROM publications p JOIN offers o ON o.id=p.offer_id WHERE p.dry_run=0 ORDER BY p.published_at DESC LIMIT 200""")
    if page == "content":
        return _rows(db, """SELECT c.id,o.title,c.channel,c.format,c.campaign_id,substr(c.body,1,160) body,c.created_at
            FROM content_packages c JOIN offers o ON o.id=c.offer_id ORDER BY c.id DESC LIMIT 200""")
    if page == "channels":
        return _rows(db, """WITH click_totals AS (SELECT channel,COUNT(*) clicks FROM clicks GROUP BY channel),
            conversion_totals AS (SELECT COALESCE(channel,'unknown') channel,COUNT(*) conversions,COALESCE(SUM(commission_cents),0) commission_cents FROM conversions WHERE status IN ('APPROVED','PAID') GROUP BY COALESCE(channel,'unknown')),
            publication_totals AS (SELECT channel,COUNT(*) publications FROM publications WHERE dry_run=0 GROUP BY channel)
            SELECT keys.channel,COALESCE(c.clicks,0) clicks,COALESCE(v.conversions,0) conversions,COALESCE(v.commission_cents,0) commission_cents,COALESCE(p.publications,0) publications
            FROM (SELECT channel FROM click_totals UNION SELECT channel FROM conversion_totals UNION SELECT channel FROM publication_totals) keys
            LEFT JOIN click_totals c USING(channel) LEFT JOIN conversion_totals v USING(channel) LEFT JOIN publication_totals p USING(channel) ORDER BY keys.channel""")
    if page == "merchants":
        return _rows(db, """WITH offer_totals AS (SELECT merchant,COUNT(*) offers FROM offers GROUP BY merchant),
            click_totals AS (SELECT merchant,COUNT(*) clicks FROM clicks GROUP BY merchant),
            conversion_totals AS (SELECT merchant,COUNT(*) conversions,COALESCE(SUM(value_cents),0) revenue_cents,COALESCE(SUM(commission_cents),0) commission_cents FROM conversions WHERE status IN ('APPROVED','PAID') GROUP BY merchant)
            SELECT o.merchant,o.offers,COALESCE(c.clicks,0) clicks,COALESCE(v.conversions,0) conversions,COALESCE(v.revenue_cents,0) revenue_cents,COALESCE(v.commission_cents,0) commission_cents
            FROM offer_totals o LEFT JOIN click_totals c USING(merchant) LEFT JOIN conversion_totals v USING(merchant) ORDER BY o.merchant""")
    if page == "affiliate-programs":
        adapters = (ShopeeManualAdapter, AmazonCreatorsAdapter, AwinFeedAdapter, MercadoLivreManualAdapter)
        counts = {row["affiliate_network"]: row["offers"] for row in _rows(db, "SELECT affiliate_network,COUNT(*) offers FROM offers GROUP BY affiliate_network")}
        return [{
            "program": adapter.name,
            "integration_status": adapter.integration_status,
            "offers": next((count for network, count in counts.items() if adapter.name.split("_")[0].lower() in network.lower()), 0),
            "capabilities": ", ".join(f"{key.value}:{value.value}" for key, value in adapter.capabilities.items()),
        } for adapter in adapters]
    if page == "clicks":
        return _rows(db, "SELECT c.click_id,o.title,c.merchant,c.channel,c.campaign_id,c.format,c.creative_id,c.timestamp FROM clicks c JOIN offers o ON o.id=c.offer_id ORDER BY c.timestamp DESC LIMIT 200")
    if page == "conversions":
        return _rows(db, "SELECT external_order_id,merchant,network,value_cents,commission_cents,status,channel,campaign,timestamp FROM conversions ORDER BY timestamp DESC LIMIT 200")
    if page == "revenue":
        return _rows(db, """SELECT merchant,network,COUNT(*) conversions,COALESCE(SUM(value_cents),0) revenue_cents,COALESCE(SUM(commission_cents),0) commission_cents
            FROM conversions WHERE status IN ('APPROVED','PAID') GROUP BY merchant,network ORDER BY commission_cents DESC""")
    if page == "analytics":
        return {"dimension": dimension, "available_dimensions": DIMENSIONS, "rows": analytics_breakdown(db, dimension)}
    if page == "compliance":
        configured = settings.merchant_channel_rules or {}
        return [{"merchant": merchant, **rule, "override": configured.get(merchant, {})} for merchant, rule in DEFAULT_MERCHANT_RULES.items()]
    if page == "settings":
        public = urlsplit(settings.public_base_url)
        safe_public_base = f"{public.scheme}://{public.hostname or ''}{f':{public.port}' if public.port else ''}"
        return {
            "dry_run": settings.dry_run,
            "public_base_url": safe_public_base,
            "web_bind_host": settings.web_bind_host,
            "admin_auth_configured": bool(settings.admin_password),
            "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "amazon_configured": bool(settings.amazon_creators_client_id and settings.amazon_creators_client_secret and settings.amazon_partner_tag_br),
            "awin_affiliate_hosts_count": len(settings.awin_allowed_affiliate_hosts),
            "awin_image_hosts_count": len(settings.awin_allowed_image_hosts),
            "channel_visibility": settings.channel_visibility or {"site": "public", "telegram": "public", "instagram": "public", "tiktok": "public"},
        }
    raise ValueError(f"pagina desconhecida: {page}")
