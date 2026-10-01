from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.curation import verified_offer_data
from app.services.site import offer_slug
from app.services.social_content import build_content_specs
from app.web import create_app


def test_unverified_reference_price_is_not_claimed_on_social_or_site(tmp_path) -> None:
    db = Database(tmp_path / "claims.db")
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="old-manual-offer",
        title="Fone",
        current_price_cents=9990,
        original_price_cents=19990,
        discount_percent=50,
        source_url="https://shopee.com.br/produto",
        affiliate_url="https://s.shopee.com.br/link",
    ))
    offer = dict(db.get_offer(offer_id))
    specs = build_content_specs(offer, "https://example.test/o/fone")
    text = "\n".join(
        str(value)
        for spec in specs
        for value in (spec.caption, *(scene["text"] for scene in spec.script))
    )
    assert "De R$" not in text
    assert "50%" not in text

    settings = Settings(
        database_path=tmp_path / "claims.db",
        public_base_url="http://127.0.0.1:8000",
        dry_run=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    page = TestClient(create_app(settings, db)).get(f"/o/{offer_slug(offer)}")
    assert page.status_code == 200
    assert "De R$" not in page.text
    assert "50%" not in page.text


def test_upgrade_cancels_unpublished_content_with_legacy_manual_claim(tmp_path) -> None:
    db = Database(tmp_path / "upgrade.db")
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="legacy",
        title="Produto legado",
        current_price_cents=9000,
        original_price_cents=20000,
        discount_percent=55,
        source_url="https://shopee.com.br/produto",
        affiliate_url="https://s.shopee.com.br/link",
    ))
    telegram_content = db.add_content(offer_id, "telegram", "#publi\nDe R$ 200,00")
    social_content = db.add_content(offer_id, "instagram", "#publi\nDe R$ 200,00", format="reel")
    db.enqueue(offer_id, telegram_content, "telegram", "organic", "creative", "legacy-draft", dry_run=False)
    social_id = db.enqueue_social(offer_id, social_content, "instagram", "reel", "READY_FOR_PUBLISH")

    db.init()

    offer = db.get_offer(offer_id)
    assert offer["original_price_cents"] is None
    assert offer["discount_percent"] is None
    assert db.rows("SELECT COUNT(*) AS count FROM publish_queue")[0]["count"] == 0
    social = db.get_social_queue(social_id)
    assert social["status"] == "CANCELLED"
    assert "gere novamente" in social["required_action"]


def test_verified_price_history_unlocks_discount_claim_across_channels(tmp_path) -> None:
    db = Database(tmp_path / "history.db")
    db.init()
    base = dict(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="observed",
        title="Fone",
        source_url="https://shopee.com.br/produto",
        affiliate_url="https://s.shopee.com.br/link",
    )
    offer_id = db.upsert_offer(Offer(**base, current_price_cents=20000, collected_at="2026-09-27T10:00:00Z"))
    db.upsert_offer(Offer(**base, current_price_cents=18000, collected_at="2026-09-28T10:00:00Z"))
    db.upsert_offer(Offer(**base, current_price_cents=9000, collected_at="2026-09-29T10:00:00Z"))
    offer = verified_offer_data(db, offer_id)
    specs = build_content_specs(offer, "https://example.test/o/fone")
    assert any("De R$ 180,00" in spec.caption for spec in specs)
    assert any("50%" in spec.caption for spec in specs)

    settings = Settings(
        database_path=tmp_path / "history.db",
        public_base_url="http://127.0.0.1:8000",
        dry_run=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    page = TestClient(create_app(settings, db)).get(f"/o/{offer_slug(offer)}")
    assert page.status_code == 200
    assert "De R$ 180,00" in page.text
    assert "50% de desconto comprovado" in page.text
