import io
from urllib.error import HTTPError

import pytest

from app.config import Settings
from app.db import Database
from app.models import Offer
from app.services.content import telegram_message, telegram_tracking_url
from app.services.curation import verified_offer_data
from app.services.pipeline import Pipeline
from app.services.telegram import TelegramClient, TelegramRateLimit


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int = -1) -> bytes:
        return b'{"ok":true,"result":{"message_id":77}}'[:size]


def test_real_telegram_spaces_messages_without_delaying_dry_run() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    client = TelegramClient(
        "secret", "@channel", dry_run=False,
        opener=lambda *_args, **_kwargs: _Response(),
        clock=lambda: now[0], sleep=sleep,
    )
    client.send_message("#publi\nA")
    client.send_message("#publi\nB")
    assert sleeps == [pytest.approx(1.05)]

    dry = TelegramClient("secret", "@channel", dry_run=True, sleep=sleep)
    dry.send_message("#publi\nA")
    dry.send_message("#publi\nB")
    assert sleeps == [pytest.approx(1.05)]


def test_telegram_429_uses_retry_after_without_exposing_token() -> None:
    def opener(request, timeout):
        body = b'{"ok":false,"error_code":429,"parameters":{"retry_after":17}}'
        raise HTTPError(request.full_url, 429, "Too Many Requests", None, io.BytesIO(body))

    client = TelegramClient("secret-token", "@channel", dry_run=False, opener=opener)
    with pytest.raises(TelegramRateLimit) as caught:
        client.send_message("#publi\nOferta")
    assert caught.value.retry_after == 17
    assert "secret-token" not in str(caught.value)


def test_429_defers_queue_without_opening_circuit_breaker(tmp_path) -> None:
    db = Database(tmp_path / "rate.db")
    db.init()
    offer_id = db.upsert_offer(Offer(
        merchant="Shopee", affiliate_network="Shopee Afiliados",
        external_product_id="rate", title="Oferta", current_price_cents=9000,
        source_url="https://shopee.com.br/p", affiliate_url="https://s.shopee.com.br/r",
    ))
    body = telegram_message(
        verified_offer_data(db, offer_id),
        telegram_tracking_url("https://offers.example", offer_id, "organic"),
    )
    content_id = db.add_content(offer_id, "telegram", body)
    queue_id = db.enqueue(offer_id, content_id, "telegram", "organic", "creative", "rate-key", dry_run=False)
    second_queue_id = db.enqueue(offer_id, content_id, "telegram", "organic", "creative-2", "rate-key-2", dry_run=False)

    class LimitedTelegram:
        calls = 0

        def send_message(self, _text):
            self.calls += 1
            raise TelegramRateLimit(17)

    settings = Settings(tmp_path / "rate.db", False, "https://offers.example", None, None)
    telegram = LimitedTelegram()
    pipeline = Pipeline(db, settings, telegram=telegram)
    results = pipeline.run_offers([])["publications"]
    assert len(results) == 1
    result = results[0]
    assert result["status"] == "RATE_LIMITED"
    state = db.channel_state("telegram")
    assert state["consecutive_failures"] == 0
    assert state["open_until"] == result["retry_at"]
    row = db.rows("SELECT status,last_error,available_at FROM publish_queue WHERE id=?", (queue_id,))[0]
    assert row["status"] == "FAILED"
    assert row["last_error"] == "Telegram aplicou limite de envio"
    assert row["available_at"] == result["retry_at"]
    assert db.rows("SELECT status FROM publish_queue WHERE id=?", (second_queue_id,))[0]["status"] == "PENDING"
    assert pipeline.process_one()["status"] == "RATE_LIMITED"
    assert telegram.calls == 1
