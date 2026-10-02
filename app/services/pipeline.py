from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.awin_feed import AwinFeedAdapter
from app.adapters.base import AffiliateAdapter
from app.adapters.mercadolivre_manual import MercadoLivreManualAdapter
from app.adapters.shopee_manual import ShopeeManualAdapter
from app.config import Settings, validate_public_base_url
from app.db import Database
from app.models import Offer, utc_now
from app.services.compliance import ComplianceError, merchant_key, validate_content, validate_distribution, validate_offer
from app.services.content import telegram_creative_id, telegram_message, telegram_tracking_url
from app.services.curation import score_offer, verified_offer_data
from app.services.dedup import is_in_cooldown
from app.services.telegram import MAX_MESSAGE_CHARS, TelegramClient, TelegramDeliveryUncertain, TelegramRateLimit


class Pipeline:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        telegram: TelegramClient | None = None,
        *,
        source_adapter: str = "shopee",
    ):
        validate_public_base_url(settings.public_base_url, dry_run=settings.dry_run)
        self.db = db
        self.settings = settings
        self.source_adapter = source_adapter
        self.telegram = telegram or TelegramClient(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            dry_run=settings.dry_run,
        )

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.settings.telegram_bot_token:
            message = message.replace(self.settings.telegram_bot_token, "[redacted]")
        if self.settings.amazon_creators_client_secret:
            message = message.replace(self.settings.amazon_creators_client_secret, "[redacted]")
        return message[:500]

    def _adapter(self, name: str) -> AffiliateAdapter:
        normalized = name.strip().lower()
        if normalized == "shopee":
            return ShopeeManualAdapter()
        if normalized == "awin":
            return AwinFeedAdapter(allowed_affiliate_hosts=self.settings.awin_allowed_affiliate_hosts)
        if normalized in {"mercadolivre", "mercado-livre", "ml"}:
            return MercadoLivreManualAdapter()
        if normalized in {"amazon-json", "amazon"}:
            raise ValueError("Amazon import bloqueado: aguarda aprovacao escrita e desenho de retencao")
        raise ValueError(f"adapter desconhecido: {name}")

    def ingest_offers(self, offers: list[Offer], *, content_campaign_id: str | None = None) -> list[int]:
        ids: list[int] = []
        for offer in offers:
            offer_data = asdict(offer)
            if merchant_key(offer_data) == "amazon":
                raise ValueError("Amazon ingest bloqueado: aguarda aprovacao escrita e desenho de retencao")
            validate_offer(offer_data)
            # Quando há campanha, o job P1 nasce na mesma transação do upsert.
            # Assim uma queda após a ingestão nunca perde o trabalho de conteúdo.
            ids.append(self.db.upsert_offer(
                offer,
                content_campaign_id=content_campaign_id,
                content_dry_run=self.settings.dry_run if content_campaign_id is not None else None,
            ))
        return ids

    def ingest_source(self, path: Path, adapter_name: str | None = None) -> list[int]:
        adapter = self._adapter(adapter_name or self.source_adapter)
        return self.ingest_offers(adapter.import_offers(path))

    def ingest_shopee(self, path: Path) -> list[int]:
        return self.ingest_source(path, "shopee")

    def curate_and_queue(self, offer_ids: list[int], campaign_id: str = "organic") -> list[int]:
        queue_ids: list[int] = []
        for offer_id in offer_ids:
            score, classification = score_offer(self.db, offer_id)
            if classification not in {"GOOD", "EXCELLENT"} or is_in_cooldown(
                self.db,
                offer_id,
                "telegram",
                campaign_id,
                dry_run=self.settings.dry_run,
            ):
                continue
            row = self.db.get_offer(offer_id)
            if row is None:
                continue
            offer = verified_offer_data(self.db, offer_id, row)
            validate_offer(offer)
            try:
                validate_distribution(offer, "telegram", self.settings)
            except ComplianceError:
                continue
            creative_id = telegram_creative_id(offer_id)
            mode = "dry" if self.settings.dry_run else "real"
            raw_key = f"telegram:{offer_id}:{offer['current_price_cents']}:{campaign_id}:{mode}"
            key = hashlib.sha256(raw_key.encode()).hexdigest()
            tracking_url = telegram_tracking_url(self.settings.public_base_url, offer_id, campaign_id, key)
            body = telegram_message(offer, tracking_url)
            try:
                validate_content(
                    body,
                    allow_http=self.settings.dry_run,
                    max_chars=MAX_MESSAGE_CHARS,
                )
            except ComplianceError:
                continue
            content_id = self.db.add_content(offer_id, "telegram", body)
            queue_ids.append(self.db.enqueue(
                offer_id,
                content_id,
                "telegram",
                campaign_id,
                creative_id,
                key,
                dry_run=self.settings.dry_run,
            ))
        return queue_ids

    def process_one(self) -> dict | None:
        queue = self.db.claim_due(dry_run=self.settings.dry_run)
        if queue is None:
            return None
        prior = self.db.publication_for_key(queue["idempotency_key"])
        if prior:
            self.db.complete_publication(queue, prior["external_message_id"])
            return {"queue_id": queue["id"], "status": "IDEMPOTENT", "message_id": prior["external_message_id"]}

        offer = verified_offer_data(self.db, queue["offer_id"])
        try:
            validate_offer(offer)
            validate_distribution(offer, queue["channel"], self.settings)
            keyed_url = telegram_tracking_url(
                self.settings.public_base_url, queue["offer_id"], queue["campaign_id"], queue["idempotency_key"],
            )
            tracking_url = keyed_url if keyed_url in queue["body"] else telegram_tracking_url(
                self.settings.public_base_url, queue["offer_id"], queue["campaign_id"],
            )
            current_body = telegram_message(offer, tracking_url)
            validate_content(
                current_body,
                allow_http=self.settings.dry_run,
                max_chars=MAX_MESSAGE_CHARS,
            )
            reason_code = "CONTENT_CHANGED" if (
                current_body != queue["body"] or offer["affiliate_url"] != queue["affiliate_url_snapshot"]
            ) else None
        except ComplianceError:
            reason_code = "OFFER_INVALID"
        if reason_code:
            self.db.discard_unpublished_queue(queue["id"], self.source_adapter, reason_code)
            return {"queue_id": queue["id"], "status": "REJECTED_STALE", "reason": reason_code}

        state = None if self.settings.dry_run else self.db.channel_state(queue["channel"])
        if state and state["open_until"] and state["open_until"] > utc_now():
            rate_limited = int(state["consecutive_failures"]) == 0
            reason = "Telegram aplicou limite de envio" if rate_limited else "circuit breaker aberto"
            self.db.fail_publication(queue["id"], reason, state["open_until"])
            return {"queue_id": queue["id"], "status": "RATE_LIMITED" if rate_limited else "CIRCUIT_OPEN"}
        try:
            result = self.telegram.send_message(queue["body"])
            if result.dry_run != self.settings.dry_run:
                raise TelegramDeliveryUncertain("publisher retornou modo diferente da fila")
        except TelegramRateLimit as exc:
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=exc.retry_after)).isoformat(timespec="seconds")
            self.db.fail_publication(queue["id"], "Telegram aplicou limite de envio", retry_at)
            if not self.settings.dry_run:
                self.db.note_channel_rate_limit(queue["channel"], retry_at)
            return {"queue_id": queue["id"], "status": "RATE_LIMITED", "retry_at": retry_at}
        except TelegramDeliveryUncertain:
            # A Bot API nao oferece chave de idempotencia para sendMessage.
            # Manter PROCESSING impede retry automatico de entrega incerta.
            return {"queue_id": queue["id"], "status": "NEEDS_RECONCILIATION"}
        except Exception as exc:
            error = self._safe_error(exc)
            attempts = int(queue["attempts"])
            delay = min(300, 2 ** min(attempts, 8))
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
            current_failures = int(state["consecutive_failures"]) if state else 0
            open_until = None
            if not self.settings.dry_run:
                open_until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds") if current_failures + 1 >= 3 else None
                self.db.note_channel_failure(queue["channel"], open_until)
            self.db.fail_publication(queue["id"], error, open_until or retry_at)
            return {"queue_id": queue["id"], "status": "FAILED", "retry_at": open_until or retry_at, "error": error}

        try:
            self.db.complete_publication(queue, result.message_id)
        except Exception:
            # O envio foi confirmado, mas a gravacao falhou; nao reenvie.
            return {"queue_id": queue["id"], "status": "NEEDS_RECONCILIATION", "message_id": result.message_id}
        if not self.settings.dry_run:
            self.db.note_channel_success(queue["channel"])
        status = "SIMULATED" if self.settings.dry_run else "PUBLISHED"
        return {"queue_id": queue["id"], "status": status, "message_id": result.message_id, "dry_run": result.dry_run}

    def run_offers(self, offers: list[Offer], campaign_id: str = "organic") -> dict:
        offer_ids = self.ingest_offers(offers, content_campaign_id=campaign_id)
        queue_ids = self.curate_and_queue(offer_ids, campaign_id)
        publications = []
        while True:
            result = self.process_one()
            if result is None:
                break
            publications.append(result)
            if result["status"] in {"FAILED", "CIRCUIT_OPEN", "RATE_LIMITED", "NEEDS_RECONCILIATION"}:
                break
        return {"offers": offer_ids, "queue": queue_ids, "publications": publications}

    def run(self, path: Path, campaign_id: str = "organic", adapter_name: str | None = None) -> dict:
        adapter = self._adapter(adapter_name or self.source_adapter)
        return self.run_offers(adapter.import_offers(path), campaign_id)
