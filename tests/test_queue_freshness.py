from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.pipeline import Pipeline


class RecordingTelegram:
    def __init__(self) -> None:
        self.calls = 0

    def send_message(self, body: str):
        self.calls += 1
        raise AssertionError("mensagem antiga nao pode ser enviada")


@pytest.mark.parametrize("changes,reason", [
    ({"current_price_cents": 12000}, "CONTENT_CHANGED"),
    ({"affiliate_url": "https://s.shopee.com.br/changed?sub_id=new"}, "CONTENT_CHANGED"),
    ({"stock_status": "OUT_OF_STOCK"}, "OFFER_INVALID"),
    ({"coupon_expiration": "2020-01-01T00:00:00Z"}, "OFFER_INVALID"),
])
def test_outdated_queued_telegram_copy_is_rejected_before_network(tmp_path, changes, reason) -> None:
    settings = Settings(
        database_path=tmp_path / "freshness.db",
        dry_run=False,
        public_base_url="https://offers.example",
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    db = Database(settings.database_path)
    db.init()
    offer = Offer(
        merchant="Shopee",
        affiliate_network="Shopee Afiliados",
        external_product_id="queued-price-change",
        title="Fone",
        current_price_cents=9000,
        source_url="https://shopee.com.br/product/1/2",
        affiliate_url="https://s.shopee.com.br/example?sub_id=test",
        coupon="CUPOM",
        shipping="Frete gratis",
        rating=4.9,
        sales_count=10000,
        commission_rate=15,
        stock_status="IN_STOCK",
    )
    offer_id = db.upsert_offer(offer)
    telegram = RecordingTelegram()
    pipeline = Pipeline(db, settings, telegram=telegram)
    assert pipeline.curate_and_queue([offer_id], "old-price")

    db.upsert_offer(replace(offer, **changes))
    result = pipeline.process_one()

    assert result["status"] == "REJECTED_STALE"
    assert result["reason"] == reason
    assert telegram.calls == 0
    assert db.rows("SELECT id FROM publications") == []
    assert db.rows("SELECT id FROM publish_queue") == []
    events = db.list_operation_events()
    assert events[0]["event"] == "queue_rejected"
    assert events[0]["status"] == reason


def test_published_queue_cannot_be_discarded(tmp_path) -> None:
    db = Database(tmp_path / "protected.db")
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee", affiliate_network="Shopee Afiliados",
        external_product_id="already-published", title="Produto", current_price_cents=9000,
        source_url="https://shopee.com.br/p", affiliate_url="https://s.shopee.com.br/link",
    ))
    content_id = db.add_content(offer_id, "telegram", "#publi\nProduto")
    queue_id = db.enqueue(offer_id, content_id, "telegram", "test", "creative", "published-key", dry_run=True)
    claimed = db.claim_due(dry_run=True)
    db.complete_publication(claimed, "dry-message")

    with pytest.raises(ValueError, match="nao pode ser descartada"):
        db.discard_unpublished_queue(queue_id, "shopee", "CONTENT_CHANGED")
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (queue_id,))[0]["status"] == "SIMULATED"
    assert len(db.rows("SELECT id FROM publications WHERE queue_id=?", (queue_id,))) == 1
