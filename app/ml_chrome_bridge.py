"""Native Messaging bridge: Chrome extension -> local bot, no HTTP listener.

Only an explicitly installed extension ID is allowed by Chrome's native host
manifest. No credential/cookie export, private API requests or login attempts.
All untrusted browser observations are validated before the existing Telegram
pipeline is permitted to queue a product.
"""
from __future__ import annotations

import json
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.db import Database
from app.mercadolivre_autopilot import (
    MLError, _queue_validated_product, affiliate_from_text, normalize_product_url,
)
from app.services.collection import is_technology_offer
from app.services.nvidia_copy import suggest_hook

MAX_MESSAGE = 48_000
PRODUCT_ID = re.compile(r"MLB-?([0-9]{5,16})(?!\d)", re.I)
ML_TECH_TOPICS = frozenset({
    "ssd", "audio", "notebook", "mouse", "teclado", "monitor", "celular",
    "ram", "tablet", "carregador", "gpu", "rede", "webcam", "placa_mae",
    "wearable", "console", "impressora", "pendrive", "gabinete", "fonte",
    "smart_home", "armazenamento", "cpu", "microfone",
})


def _topic_busy(db: Database, topic: str, *, now: datetime | None = None) -> bool:
    if topic not in ML_TECH_TOPICS:
        raise MLError("CATEGORIA_INVALIDA")
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
    rows = db.rows(
        """SELECT 1 FROM offers o
           WHERE o.affiliate_network='Mercado Livre Afiliados e Criadores'
             AND json_extract(o.tracking_metadata_json,'$.ml_topic')=?
             AND (
                EXISTS (SELECT 1 FROM publications p
                        WHERE p.offer_id=o.id AND p.dry_run=0
                          AND p.published_at>=?)
                OR EXISTS (SELECT 1 FROM content_jobs j
                           WHERE j.offer_id=o.id
                             AND j.status IN ('PENDING','PROCESSING'))
             )
           LIMIT 1""",
        (topic, cutoff),
    )
    return bool(rows)


def _queue(payload: dict[str, Any], settings: Settings, db: Database) -> dict[str, Any]:
    if not settings.telegram_only_mode:
        raise MLError("TELEGRAM_ONLY_NAO_CONFIGURADO")
    if not settings.dry_run and not settings.telegram_real_enabled:
        raise MLError("ENVIO_REAL_NAO_HABILITADO")
    product = payload.get("product")
    affiliate = payload.get("affiliate_url")
    if not isinstance(product, dict) or not isinstance(affiliate, str):
        raise MLError("PRODUTO_OU_LINK_AUSENTE")
    source = product.get("source_url")
    title = product.get("title")
    price = product.get("price_cents")
    if not isinstance(source, str) or normalize_product_url(source) != source:
        raise MLError("URL_DO_PRODUTO_INVALIDA")
    match = PRODUCT_ID.search(source)
    if not match:
        raise MLError("ID_DE_PRODUTO_INVALIDO")
    external_id = "MLB" + match.group(1)
    if not isinstance(title, str) or not 6 <= len(title) <= 250:
        raise MLError("TITULO_INVALIDO")
    if not is_technology_offer(
        {"title": title, "category": "", "brand": ""},
        terms=settings.technology_offer_terms,
    ):
        raise MLError("PRODUTO_FORA_DO_NICHO")
    if type(price) is not int or not 100 <= price <= 50_000_000:
        raise MLError("PRECO_INVALIDO")
    # The previous extension read the first DOM price: it could be a crossed-out
    # or non-discounted amount. Reject legacy/no-evidence price payloads.
    if product.get("price_evidence") != "VISIBLE_DOM_PRICE_V2":
        raise MLError("PRECO_SEM_VALIDACAO_DE_DESCONTO")
    method = product.get("price_payment_method")
    if method not in ("PIX", "STANDARD"):
        raise MLError("CONDICAO_DE_PAGAMENTO_INVALIDA")
    alternate = product.get("other_price_cents")
    if alternate is not None and (
        method != "PIX" or type(alternate) is not int
        or not price < alternate <= 50_000_000
    ):
        raise MLError("PRECO_ALTERNATIVO_INVALIDO")
    count = product.get("installment_count")
    installment = product.get("installment_cents")
    interest_free = product.get("installment_interest_free")
    with_interest = product.get("installment_with_interest")
    if any(value is not None for value in (count, installment, interest_free, with_interest)):
        if (
            type(count) is not int or not 2 <= count <= 24
            or type(installment) is not int or not 100 <= installment <= 50_000_000
            or type(interest_free) is not bool or type(with_interest) is not bool
            or (interest_free and with_interest)
            or count * installment < price * 0.9
            or count * installment > price * 2.5
        ):
            raise MLError("PARCELAMENTO_SEM_COMPROVACAO")
    shipping_evidence = product.get("shipping_evidence")
    shipping_free = product.get("shipping_free")
    shipping_tomorrow = product.get("shipping_tomorrow")
    shipping_full = product.get("shipping_full")
    shipping_flags = (shipping_free, shipping_tomorrow, shipping_full)
    # These optional facts must be explicitly verified in the product
    # delivery area. Older versions of the extension remain compatible.
    if shipping_evidence is None:
        if any(value is not None and value is not False for value in shipping_flags):
            raise MLError("ENTREGA_SEM_COMPROVACAO")
    elif (
        shipping_evidence != "VISIBLE_DOM_SHIPPING_V1"
        or any(type(value) is not bool for value in shipping_flags)
        or not any(shipping_flags)
    ):
        raise MLError("ENTREGA_SEM_COMPROVACAO")
    if product.get("buy_now_enabled") is not True:
        raise MLError("COMPRA_NAO_CONFIRMADA")
    if affiliate_from_text(affiliate) != affiliate:
        raise MLError("LINK_DE_AFILIADO_INVALIDO")
    # Never accept the timestamp supplied by a webpage as evidence of
    # freshness. The native host records local UTC time when the browser
    # observations arrive, then the queue gate expires after 15 minutes.
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = db.rows(
        """SELECT 1 FROM offers o
           WHERE o.affiliate_network='Mercado Livre Afiliados e Criadores'
             AND o.external_product_id=? AND EXISTS (
                SELECT 1 FROM publications p
                WHERE p.offer_id=o.id AND p.dry_run=0 AND p.published_at>=?
           ) LIMIT 1""",
        (external_id, since),
    )
    if rows:
        return {"status": "ALREADY_PUBLISHED", "queued": 0}
    # A single product category may not dominate a rapid campaign.
    topic = payload.get("topic")
    if not isinstance(topic, str) or topic not in ML_TECH_TOPICS:
        raise MLError("CATEGORIA_INVALIDA")
    if _topic_busy(db, topic, now=now):
        return {"status": "TOPIC_COOLDOWN", "queued": 0}
    existing = db.rows(
        """SELECT 1 FROM offers o
           WHERE o.affiliate_network='Mercado Livre Afiliados e Criadores'
             AND o.external_product_id=? AND EXISTS (
                SELECT 1 FROM content_jobs j
                WHERE j.offer_id=o.id AND j.dry_run=? AND j.status IN ('PENDING','PROCESSING')
           ) LIMIT 1""",
        (external_id, int(settings.dry_run)),
    )
    if existing:
        return {"status": "ALREADY_QUEUED", "queued": 0}
    details = {
        "external_id": external_id,
        "source_url": source,
        "title": title,
        "price_cents": price,
        "price_evidence": "VISIBLE_DOM_PRICE_V2",
        "price_payment_method": method,
        "other_price_cents": alternate,
        "installment_count": count,
        "installment_cents": installment,
        "installment_interest_free": interest_free,
        "installment_with_interest": with_interest,
        "shipping_evidence": shipping_evidence,
        "shipping_free": shipping_free is True,
        "shipping_tomorrow": shipping_tomorrow is True,
        "shipping_full": shipping_full is True,
        "ml_topic": topic,
        "stock_status": "IN_STOCK",
        "confirmed_at": now.isoformat(timespec="seconds"),
    }
    # Optional bounded text-only API call. Failed/slow NVIDIA replies never
    # break the publication flow and never alter verified commercial facts.
    if settings.ml_copy_nemotron_enabled and settings.nvidia_api_key:
        details["copy_hook"] = suggest_hook(
            title, api_key=settings.nvidia_api_key,
        )
    result = _queue_validated_product(db, settings, details, affiliate)
    return {
        "status": result["status"],
        "queued": len(result["offer_ids"]),
        "published_now": 0,
    }


def process(message: object, *, settings: Settings | None = None, db: Database | None = None) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {"status": "BLOCKED", "reason": "MENSAGEM_INVALIDA"}
    action = message.get("action")
    if action == "ping":
        return {"status": "OK", "bridge": "ML_CHROME", "version": 1}
    if action == "topicStatus":
        try:
            settings = settings or Settings.from_env()
            db = db or Database(settings.database_path)
            db.init()
            topic = message.get("topic")
            if not isinstance(topic, str) or topic not in ML_TECH_TOPICS:
                return {"status": "BLOCKED", "reason": "CATEGORIA_INVALIDA"}
            return {"status": "TOPIC_COOLDOWN" if _topic_busy(db, topic) else "OK"}
        except Exception:
            return {"status": "BLOCKED", "reason": "VERIFICACAO_LOCAL_FALHOU"}
    if action != "queue":
        return {"status": "BLOCKED", "reason": "ACAO_NAO_PERMITIDA"}
    try:
        settings = settings or Settings.from_env()
        db = db or Database(settings.database_path)
        db.init()
        return _queue(message, settings, db)
    except (MLError, ValueError, KeyError, TypeError):
        return {"status": "BLOCKED", "reason": "OFERTA_INVALIDA_OU_NAO_PERMITIDA"}
    except Exception:
        # Deliberately never expose stack traces, environment variables,
        # tokens or SQLite path to the browser extension.
        return {"status": "BLOCKED", "reason": "FALHA_LOCAL_CONSULTE_LOGS"}


def _read_frame(stream) -> object:
    header = stream.read(4)
    if len(header) != 4:
        raise ValueError("sem frame")
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_MESSAGE:
        raise ValueError("tamanho nao permitido")
    blob = stream.read(length)
    if len(blob) != length:
        raise ValueError("frame incompleto")
    return json.loads(blob.decode("utf-8"))


def _write_frame(stream, reply: dict[str, Any]) -> None:
    blob = json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("=I", len(blob)))
    stream.write(blob)
    stream.flush()


def main() -> None:
    try:
        request = _read_frame(sys.stdin.buffer)
        answer = process(request)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        answer = {"status": "BLOCKED", "reason": "FRAME_INVALIDO"}
    _write_frame(sys.stdout.buffer, answer)


if __name__ == "__main__":
    main()
