from __future__ import annotations

import base64
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.amazon_creators import AGENT_USER_AGENT, AmazonCreatorsAdapter, AmazonCreatorsError
from app.adapters.awin_feed import AwinFeedAdapter
from app.cli import main as cli_main
from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.analytics import DIMENSIONS, analytics_breakdown
from app.services.llm import TemplateProvider
from app.services.compliance import ComplianceError, validate_distribution, validate_offer
from app.services.compliance import allows_tracking_redirect, distribution_review
from app.services.curation import score_offer
from app.services.media import _approved_image_url
from app.services.media import CreativeGenerator
from app.services.p1 import P1Pipeline
from app.services.pipeline import Pipeline
from app.services.site import offer_slug
from app.web import create_app


ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path, **changes) -> Settings:
    base = Settings(
        database_path=tmp_path / "p3.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        creatives_path=tmp_path / "creatives",
        ffmpeg_path=str(tmp_path / "missing-ffmpeg"),
    )
    return replace(base, **changes)


def offer(*, product_id: str, category: str = "Eletronicos", merchant: str = "Shopee", network: str = "Shopee Afiliados", expires_at: str | None = None) -> Offer:
    return Offer(
        merchant=merchant,
        affiliate_network=network,
        external_product_id=product_id,
        title=f"Produto {product_id}",
        category=category,
        original_price_cents=20000,
        current_price_cents=10000,
        discount_percent=50,
        coupon="CUPOM",
        shipping="Frete gratis",
        rating=4.9,
        sales_count=5000,
        commission_rate=10,
        source_url="https://shopee.com.br/product/1/2",
        affiliate_url="https://s.shopee.com.br/example?sub_id=p3",
        stock_status="IN_STOCK",
        expires_at=expires_at,
    )


def auth(password: str) -> dict[str, str]:
    value = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def add_conversion(db: Database, *, order: str, offer_id: int, click_id: str, commission: int = 500) -> None:
    db.import_conversion({
        "external_order_id": order,
        "offer_id": offer_id,
        "click_id": click_id,
        "merchant": "Shopee",
        "network": "Shopee Afiliados",
        "value_cents": 10000,
        "commission_cents": commission,
        "status": "APPROVED",
        "channel": "telegram",
        "campaign": "p3",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def test_database_context_closes_connection(tmp_path: Path) -> None:
    db = Database(tmp_path / "connection.db")
    db.init()
    with db.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_dynamic_web_responses_include_defensive_security_headers(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        public_base_url="https://afiliados.example",
        admin_password="secret",
    )
    db = Database(settings.database_path)
    db.init()
    client = TestClient(create_app(settings, db), base_url="https://afiliados.example")

    public = client.get("/offers")
    assert public.status_code == 200
    assert public.headers["x-content-type-options"] == "nosniff"
    assert public.headers["x-frame-options"] == "DENY"
    assert public.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert public.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert "frame-ancestors 'none'" in public.headers["content-security-policy"]
    assert public.headers["cache-control"] == "no-store"

    rejected = client.get("/")
    assert rejected.status_code == 401
    assert rejected.headers["x-content-type-options"] == "nosniff"
    assert rejected.headers["cache-control"] == "no-store"

    docs_rejected = client.get("/docs")
    assert docs_rejected.status_code == 401
    assert docs_rejected.headers["x-content-type-options"] == "nosniff"

    docs = client.get("/docs", headers=auth("secret"))
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers
    assert docs.headers["x-content-type-options"] == "nosniff"

    assert client.get("/openapi.json").status_code == 401
    assert client.get("/openapi.json", headers=auth("secret")).status_code == 200


def test_admin_basic_auth_public_routes_and_secret_redaction(tmp_path: Path) -> None:
    secret = "unique-admin-password"
    settings = settings_for(
        tmp_path,
        public_base_url="https://afiliados.example",
        admin_password=secret,
        telegram_bot_token="telegram-secret-value",
        amazon_creators_client_secret="amazon-secret-value",
    )
    db = Database(settings.database_path)
    db.init()
    app = create_app(settings, db)
    client = TestClient(app, base_url="https://afiliados.example")
    assert TestClient(app, base_url="http://afiliados.example").get("/", headers=auth(secret)).status_code == 426

    assert client.get("/health").status_code == 200
    assert client.get("/offers").status_code == 200
    assert client.get("/").status_code == 401
    assert client.get("/api/overview").status_code == 401
    assert client.get("/", headers=auth("wrong")).status_code == 401
    assert client.get("/", headers=auth(secret)).status_code == 200

    for slug in (
        "offers", "queue", "published", "content", "channels", "merchants",
        "affiliate-programs", "clicks", "conversions", "revenue", "analytics",
        "compliance", "settings",
    ):
        response = client.get(f"/admin/{slug}", headers=auth(secret))
        assert response.status_code == 200, slug
    assert "Sem dados reais" in client.get("/admin/published", headers=auth(secret)).text
    settings_response = client.get("/api/admin/settings", headers=auth(secret))
    body = settings_response.text
    assert settings_response.status_code == 200
    assert secret not in body
    assert "telegram-secret-value" not in body
    assert "amazon-secret-value" not in body


def test_public_bind_fails_closed_without_https_and_password(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        create_app(settings_for(tmp_path, public_base_url="https://afiliados.example"))
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(settings_for(tmp_path, public_base_url="http://afiliados.example", admin_password="secret"))
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        create_app(settings_for(tmp_path, web_bind_host="0.0.0.0"))


def test_segmented_analytics_use_real_impressions_and_approved_conversions(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    offer_id = db.upsert_offer(offer(product_id="analytics"))
    row = db.get_offer(offer_id)
    click_id = db.record_click(
        row,
        channel="telegram",
        campaign_id="p3",
        creative_id="creative-1",
        format="text",
        referrer=None,
        utm_source=None,
        utm_medium=None,
        utm_campaign=None,
        sub_id=None,
    )
    add_conversion(db, order="order-1", offer_id=offer_id, click_id=click_id)

    before = analytics_breakdown(db, "channel")[0]
    assert before["ctr"] is None
    assert before["cvr"] == 100.0
    assert before["epc_cents"] == 500.0
    assert before["commission_per_post_cents"] is None

    db.record_impression(offer_id, channel="telegram", campaign_id="p3", format="text", creative_id="creative-1", source="official-export")
    db.record_impression(offer_id, channel="telegram", campaign_id="p3", format="text", creative_id="creative-1", source="official-export")
    after = analytics_breakdown(db, "channel")[0]
    assert after["ctr"] == 50.0
    assert db.overview()["impressions"] == 2
    assert db.overview()["ctr"] == 50.0
    for dimension in DIMENSIONS:
        assert analytics_breakdown(db, dimension), dimension


def test_cvr_and_epc_ignore_unattributed_conversions_but_keep_financial_totals(tmp_path: Path) -> None:
    db = Database(tmp_path / "attributed-metrics.db")
    db.init()
    offer_id = db.upsert_offer(offer(product_id="attributed-metrics"))
    row = db.get_offer(offer_id)
    assert row is not None

    click_id = db.record_click(
        row,
        channel="telegram",
        campaign_id="p3",
        creative_id="creative-1",
        format="text",
        referrer=None,
        utm_source=None,
        utm_medium=None,
        utm_campaign=None,
        sub_id=None,
    )
    add_conversion(db, order="attributed-order", offer_id=offer_id, click_id=click_id, commission=500)
    db.import_conversion({
        "external_order_id": "unattributed-order",
        "offer_id": offer_id,
        "click_id": None,
        "merchant": "Shopee",
        "network": "Shopee Afiliados",
        "value_cents": 50000,
        "commission_cents": 5000,
        "status": "APPROVED",
        "channel": "telegram",
        "campaign": "p3",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    channel = next(row for row in analytics_breakdown(db, "channel") if row["segment"] == "telegram")
    assert channel["clicks"] == 1
    assert channel["conversions"] == 2
    assert channel["attributed_conversions"] == 1
    assert channel["commission_cents"] == 5500
    assert channel["attributed_commission_cents"] == 500
    assert channel["cvr"] == 100.0
    assert channel["epc_cents"] == 500.0

    overview = db.overview()
    assert overview["conversions"] == 2
    assert overview["commission_cents"] == 5500
    assert overview["cvr"] == 100.0
    assert overview["epc_cents"] == 500.0


def test_feedback_requires_volume_and_changes_priority_from_real_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "feedback.db")
    db.init()
    a_id = db.upsert_offer(offer(product_id="a", category="Categoria A"))
    b_id = db.upsert_offer(offer(product_id="b", category="Categoria B"))
    fixed_time = datetime.now(timezone.utc)
    initial_a = score_offer(db, a_id, at=fixed_time)[0]
    initial_b = score_offer(db, b_id, at=fixed_time)[0]
    assert initial_a == initial_b

    a = db.get_offer(a_id)
    first = db.record_click(a, channel="telegram", campaign_id="p3", creative_id="a", format="text", referrer=None, utm_source=None, utm_medium=None, utm_campaign=None, sub_id=None)
    add_conversion(db, order="a-0", offer_id=a_id, click_id=first)
    assert score_offer(db, a_id, at=fixed_time)[0] == initial_a

    for index in range(1, 6):
        click_id = db.record_click(a, channel="telegram", campaign_id="p3", creative_id="a", format="text", referrer=None, utm_source=None, utm_medium=None, utm_campaign=None, sub_id=None)
        if index <= 3:
            add_conversion(db, order=f"a-{index}", offer_id=a_id, click_id=click_id)
    b = db.get_offer(b_id)
    for index in range(6):
        db.record_click(b, channel="telegram", campaign_id="p3", creative_id="b", format="text", referrer=None, utm_source=None, utm_medium=None, utm_campaign=None, sub_id=None)

    assert score_offer(db, a_id, at=fixed_time)[0] > score_offer(db, b_id, at=fixed_time)[0]


def test_freshness_and_merchant_channel_rules_block_unsafe_distribution(tmp_path: Path) -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    stale = offer(product_id="stale", expires_at=past)
    with pytest.raises(ComplianceError, match="oferta expirada"):
        validate_offer(stale.__dict__ if hasattr(stale, "__dict__") else {field: getattr(stale, field) for field in stale.__slots__})

    ml = offer(product_id="ml", merchant="Mercado Livre", network="Mercado Livre Afiliados e Criadores")
    settings = settings_for(tmp_path, channel_visibility={"telegram": "private"})
    with pytest.raises(ComplianceError, match="nao esta configurado como publico"):
        validate_distribution({field: getattr(ml, field) for field in ml.__slots__}, "telegram", settings)

    db = Database(settings.database_path)
    db.init()
    stale_id = db.upsert_offer(stale)
    client = TestClient(create_app(settings, db))
    catalog = client.get("/offers").text
    assert "Preço aguardando atualização" in catalog
    page = client.get(f"/o/{offer_slug(db.get_offer(stale_id))}")
    assert "Oferta aguardando atualização" in page.text
    assert "Ir para a oferta" not in page.text


def test_amazon_is_fail_closed_without_written_approval_and_never_uses_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_for(tmp_path, amazon_partner_tag_br="exemplo-20")
    db = Database(settings.database_path)
    db.init()
    adapter = AmazonCreatorsAdapter(None, None, "exemplo-20")
    with pytest.raises(AmazonCreatorsError, match="Amazon live bloqueada"):
        adapter.search_items("produto")
    amazon_offer = adapter.import_offers(ROOT / "examples" / "amazon_creators_search.sample.json")[0]
    assert amazon_offer.image_urls == []
    override = replace(settings, merchant_channel_rules={"amazon": {"merchant_review": False, "allow_redirect": True}})
    amazon_dict = {field: getattr(amazon_offer, field) for field in amazon_offer.__slots__}
    assert distribution_review(amazon_dict, "telegram", override).startswith("PENDING_MERCHANT_REVIEW")
    assert not allows_tracking_redirect(amazon_dict, override)
    offer_id = db.upsert_offer(amazon_offer)
    client = TestClient(create_app(settings, db))
    slug = offer_slug(db.get_offer(offer_id))
    assert client.get(f"/o/{slug}").status_code == 403
    assert client.get(f"/go/{offer_id}").status_code == 403
    assert "Produto Amazon Exemplo" not in client.get("/offers").text
    assert Pipeline(db, settings).curate_and_queue([offer_id], "amazon") == []

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr("sys.argv", ["app.cli", "amazon-search", "produto"])
    with pytest.raises(RuntimeError, match="aprovacao escrita"):
        cli_main()


def test_admitad_requires_program_review_before_distribution_or_redirect(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    offer_data = {"merchant": "Loja parceira", "affiliate_network": "Admitad"}
    assert distribution_review(offer_data, "telegram", settings).startswith("PENDING_MERCHANT_REVIEW")
    assert not allows_tracking_redirect(offer_data, settings)

    redirect_only = replace(settings, merchant_channel_rules={"admitad": {"allow_redirect": True}})
    assert not allows_tracking_redirect(offer_data, redirect_only)

    approved = replace(settings, merchant_channel_rules={"admitad": {"merchant_review": False, "allow_redirect": True}})
    assert distribution_review(offer_data, "telegram", approved) is None
    assert allows_tracking_redirect(offer_data, approved)


@pytest.mark.parametrize("merchant,network,key", [
    ("Influenciador Magalu", "Parceiro Magalu", "magalu"),
    ("AliExpress", "AliExpress Affiliate", "aliexpress"),
    ("SHEIN", "SHEIN Affiliate", "shein"),
])
def test_unverified_p3_programs_require_review_before_distribution_or_redirect(
    tmp_path: Path, merchant: str, network: str, key: str
) -> None:
    settings = settings_for(tmp_path)
    offer_data = {"merchant": merchant, "affiliate_network": network}
    assert distribution_review(offer_data, "telegram", settings).startswith("PENDING_MERCHANT_REVIEW")
    assert not allows_tracking_redirect(offer_data, settings)
    redirect_only = replace(settings, merchant_channel_rules={key: {"allow_redirect": True}})
    assert not allows_tracking_redirect(offer_data, redirect_only)
    offer_data["affiliate_network"] = "Awin"
    assert distribution_review(offer_data, "telegram", settings).startswith("PENDING_MERCHANT_REVIEW")


def test_shein_offer_stays_out_of_public_hub_and_telegram_queue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    shein = replace(
        offer(product_id="shein-unverified", merchant="SHEIN", network="SHEIN Affiliate"),
        source_url="https://br.shein.com/example",
        affiliate_url="https://onelink.shein.com/3/example",
    )
    offer_id = db.upsert_offer(shein)
    client = TestClient(create_app(settings, db))
    assert "Produto shein-unverified" not in client.get("/offers").text
    assert client.get(f"/o/{offer_slug(db.get_offer(offer_id))}").status_code == 403
    assert client.get(f"/go/{offer_id}").status_code == 403
    assert db.rows("SELECT click_id FROM clicks") == []
    assert Pipeline(db, settings).curate_and_queue([offer_id], "unverified") == []


def test_amazon_content_generation_is_fail_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    db = Database(settings.database_path)
    db.init()
    amazon = replace(
        offer(product_id="amazon-social", merchant="Amazon.com.br", network="Amazon Associados"),
        source_url="https://www.amazon.com.br/dp/B0EXAMPLE?tag=example-20",
        affiliate_url="https://www.amazon.com.br/dp/B0EXAMPLE?tag=example-20",
        deeplink="https://www.amazon.com.br/dp/B0EXAMPLE?tag=example-20",
        source_type="AMAZON_CREATORS_API",
        image_urls=[],
    )
    offer_id = db.upsert_offer(amazon)
    generator = CreativeGenerator(settings.creatives_path, ffmpeg_path=settings.ffmpeg_path, image_loader=lambda _: None)
    result = P1Pipeline(db, settings, generator, provider=TemplateProvider()).generate_offer(
        offer_id, "amazon-review",
    )
    assert result["status"] == "PENDING_MERCHANT_REVIEW"
    assert result["packages"] == []
    assert db.rows("SELECT COUNT(*) n FROM content_packages")[0]["n"] == 0
    assert not settings.creatives_path.exists()


def test_awin_freshness_user_agent_and_image_allowlist() -> None:
    awin = AwinFeedAdapter().import_offers(ROOT / "examples" / "awin_feed.sample.csv")[0]
    collected = datetime.fromisoformat(awin.collected_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(awin.expires_at).replace("Z", "+00:00"))
    assert expires - collected == timedelta(hours=24)
    assert AGENT_USER_AGENT.startswith("Agent/")
    assert not _approved_image_url("https://images.example/product.jpg")
    assert _approved_image_url("https://images.example/product.jpg", ("images.example",))
    assert not _approved_image_url("https://images.example.evil.test/product.jpg", ("images.example",))
    assert not _approved_image_url("https://m.media-amazon.com/images/I/example.jpg")
