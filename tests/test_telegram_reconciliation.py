from __future__ import annotations

import io
import json
import sys
from urllib.error import HTTPError, URLError

import pytest

from app.cli import _run_recorded, main
from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.content import telegram_message, telegram_tracking_url
from app.services.curation import verified_offer_data
from app.services.pipeline import Pipeline
from app.services.telegram import TelegramClient, TelegramDeliveryUncertain, TelegramResult


def _queued_offer(tmp_path):
    db = Database(tmp_path / "telegram.db")
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee", affiliate_network="Shopee Afiliados",
        external_product_id="reconcile", title="Oferta", current_price_cents=9000,
        source_url="https://shopee.com.br/p", affiliate_url="https://s.shopee.com.br/r?sub_id=abc",
    ))
    settings = Settings(db.path, False, "https://offers.example", None, None)
    body = telegram_message(
        verified_offer_data(db, offer_id),
        telegram_tracking_url(settings.public_base_url, offer_id, "reconcile"),
    )
    content_id = db.add_content(offer_id, "telegram", body)
    queue_id = db.enqueue(offer_id, content_id, "telegram", "reconcile", "creative", "reconcile-key", dry_run=False)
    return db, settings, queue_id


@pytest.mark.parametrize("failure", ["transport", "server", "malformed"])
def test_unconfirmed_telegram_response_never_enables_auto_retry(failure) -> None:
    class MalformedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b"not json"

    def opener(request, timeout):
        assert timeout == 20
        if failure == "transport":
            raise URLError("connection lost")
        if failure == "server":
            raise HTTPError(request.full_url, 502, "bad gateway", None, io.BytesIO(b"{}"))
        return MalformedResponse()

    client = TelegramClient("secret", "@channel", dry_run=False, opener=opener)
    with pytest.raises(TelegramDeliveryUncertain):
        client.send_message("#publi\nOferta")


def test_ambiguous_delivery_stays_processing_and_can_be_confirmed_published(tmp_path, monkeypatch, capsys) -> None:
    db, settings, queue_id = _queued_offer(tmp_path)

    class UncertainTelegram:
        calls = 0

        def send_message(self, _body):
            self.calls += 1
            raise TelegramDeliveryUncertain("no confirmed result")

    telegram = UncertainTelegram()
    pipeline = Pipeline(db, settings, telegram=telegram)
    cycle = _run_recorded(db, "real", "shopee", lambda: pipeline.run_offers([]))
    assert cycle["publications"] == [{"queue_id": queue_id, "status": "NEEDS_RECONCILIATION"}]
    assert telegram.calls == 1
    assert pipeline.process_one() is None
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (queue_id,))[0]["status"] == "PROCESSING"
    assert db.rows("SELECT id FROM publications") == []
    assert db.list_operation_events()[0]["status"] == "PARTIAL"

    monkeypatch.setenv("DATABASE_PATH", str(db.path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(sys, "argv", ["app.cli", "telegram-processing"])
    main()
    processing = json.loads(capsys.readouterr().out)
    assert processing[0]["queue_id"] == queue_id
    assert "affiliate_url" not in processing[0]

    with db.connect() as connection:
        connection.execute("UPDATE offers SET affiliate_url='https://s.shopee.com.br/new' WHERE id=(SELECT offer_id FROM publish_queue WHERE id=?)", (queue_id,))

    monkeypatch.setattr(sys, "argv", ["app.cli", "telegram-reconcile", str(queue_id), "--published-message-id", "77", "--confirm-worker-stopped"])
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "PUBLISHED"
    publication = db.rows("SELECT * FROM publications WHERE queue_id=?", (queue_id,))[0]
    assert publication["external_message_id"] == "77"
    assert publication["affiliate_url"] == "https://s.shopee.com.br/r?sub_id=abc"
    assert pipeline.process_one() is None
    with pytest.raises(ValueError, match="PROCESSING"):
        db.reconcile_telegram_queue(queue_id, published_message_id="78", confirmed_not_published=False)


def test_confirmed_absence_discards_queue_and_requires_fresh_cycle(tmp_path) -> None:
    db, settings, queue_id = _queued_offer(tmp_path)
    db.claim_due(dry_run=False)
    with pytest.raises(ValueError, match="message_id"):
        db.reconcile_telegram_queue(queue_id, published_message_id="abc", confirmed_not_published=False)
    with pytest.raises(ValueError, match="message_id"):
        db.reconcile_telegram_queue(queue_id, published_message_id=" ", confirmed_not_published=False)
    with pytest.raises(ValueError, match="ambos"):
        db.reconcile_telegram_queue(queue_id, published_message_id="77", confirmed_not_published=True)

    result = db.reconcile_telegram_queue(queue_id, published_message_id=None, confirmed_not_published=True)
    assert result == {"queue_id": queue_id, "status": "NOT_PUBLISHED"}
    assert db.rows("SELECT id FROM publish_queue WHERE id=?", (queue_id,)) == []
    assert db.rows("SELECT id FROM publications") == []
    assert db.list_operation_events()[0]["status"] == "NOT_PUBLISHED"
    assert Pipeline(db, settings).process_one() is None


def test_confirmed_send_with_persistence_failure_stays_processing(tmp_path, monkeypatch) -> None:
    db, settings, queue_id = _queued_offer(tmp_path)

    class SuccessfulTelegram:
        calls = 0

        def send_message(self, _body):
            self.calls += 1
            return TelegramResult("91", dry_run=False)

    def fail_complete(*_args):
        raise OSError("database unavailable")

    monkeypatch.setattr(db, "complete_publication", fail_complete)
    telegram = SuccessfulTelegram()
    pipeline = Pipeline(db, settings, telegram=telegram)
    result = pipeline.process_one()
    assert result == {"queue_id": queue_id, "status": "NEEDS_RECONCILIATION", "message_id": "91"}
    assert pipeline.process_one() is None
    assert telegram.calls == 1
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (queue_id,))[0]["status"] == "PROCESSING"
    assert db.rows("SELECT id FROM publications") == []
