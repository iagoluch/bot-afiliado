from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.adapters.admitad_feed import AdmitadFeedAdapter
from app.config import Settings
from app.db import Database
from app.scheduler import run_tick
from app.services.conversions import import_conversion_csv
from app.services.llm import copy_preview
from app.services.instagram_graph import InstagramReelPublisher
from app.services.pipeline import Pipeline
from app.services.p1 import P1Pipeline


def _record_operation(db: Database, mode: str, adapter: str, event: str, status: str, **counts: int | str | None) -> None:
    try:
        db.record_operation_event(event, mode, adapter, status, **counts)
    except Exception as exc:
        # A falha de log nunca pode fazer o agendador repetir uma postagem ja enviada.
        print(json.dumps({"event": "operation_log_failed", "error_type": type(exc).__name__}), file=sys.stderr)


def _run_recorded(db: Database, mode: str, adapter: str, action: Callable[[], dict]) -> dict:
    try:
        result = action()
    except Exception as exc:
        _record_operation(db, mode, adapter, "cycle", "FAILED", error_type=type(exc).__name__)
        raise

    if result.get("status") == "idle":
        return result
    if result.get("status") == "queue":
        queue = result["result"]
        _record_operation(db, mode, adapter, "queue", queue["status"], queue_id=queue["queue_id"])
    else:
        cycle = result["result"] if result.get("status") == "cycle" else result
        publications = cycle["publications"]
        failed = any(item["status"] in {"FAILED", "RATE_LIMITED", "CIRCUIT_OPEN", "REJECTED_STALE", "NEEDS_RECONCILIATION"} for item in publications)
        _record_operation(
            db, mode, adapter, "cycle", "PARTIAL" if failed else "COMPLETED",
            offers=len(cycle["offers"]), queued=len(cycle["queue"]), processed=len(publications),
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Motor local de ofertas afiliadas")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    cycle = sub.add_parser("cycle")
    cycle.add_argument("source", type=Path)
    cycle.add_argument("--campaign", default="manual")
    cycle.add_argument("--adapter", choices=("shopee", "awin", "mercadolivre"), default="shopee")
    p1_cycle = sub.add_parser("p1-cycle")
    p1_cycle.add_argument("source", type=Path)
    p1_cycle.add_argument("--campaign", default="p1-manual")
    p1_cycle.add_argument("--adapter", choices=("shopee", "awin", "mercadolivre"), default="shopee")
    p1_offer = sub.add_parser("p1-offer")
    p1_offer.add_argument("offer_id", type=int)
    p1_offer.add_argument("--campaign", default="p1-manual")
    conversions = sub.add_parser("import-conversions")
    conversions.add_argument("source", type=Path)
    scheduled = sub.add_parser("scheduler-once")
    scheduled.add_argument("source", type=Path)
    scheduled.add_argument("--adapter", choices=("shopee", "awin", "mercadolivre"), default="shopee")
    importer = sub.add_parser("import-offers")
    importer.add_argument("source", type=Path)
    importer.add_argument("--adapter", choices=("shopee", "awin", "mercadolivre", "admitad"), default="shopee")
    importer.add_argument("--merchant-name", help="Nome do programa aprovado no export Admitad")
    importer.add_argument("--delimiter", default=",", help="Separador do CSV Admitad")
    amazon = sub.add_parser("amazon-search")
    amazon.add_argument("keywords")
    amazon.add_argument("--item-count", type=int, default=10)
    sub.add_parser("overview")
    events = sub.add_parser("events", help="Ultimos eventos operacionais estruturados")
    events.add_argument("--limit", type=int, default=50)
    telegram_processing = sub.add_parser("telegram-processing", help="Lista envios reais que exigem reconciliacao")
    telegram_processing.add_argument("--limit", type=int, default=50)
    telegram_reconcile = sub.add_parser("telegram-reconcile", help="Reconcilia um envio Telegram apos verificar o canal")
    telegram_reconcile.add_argument("queue_id", type=int)
    telegram_reconcile.add_argument("--confirm-worker-stopped", action="store_true", required=True)
    telegram_outcome = telegram_reconcile.add_mutually_exclusive_group(required=True)
    telegram_outcome.add_argument("--published-message-id")
    telegram_outcome.add_argument("--confirmed-not-published", action="store_true")
    sub.add_parser("social-queue")
    preview = sub.add_parser("copy-preview", help="Rascunho editorial local sem publicacao")
    preview.add_argument("offer_id", type=int)
    preview.add_argument("--channel", choices=("telegram", "instagram_feed", "instagram_story", "instagram_reel", "tiktok", "site"), default="instagram_feed")
    instagram_create = sub.add_parser("instagram-reel-create", help="Cria container Reel apos revisao explicita")
    instagram_create.add_argument("queue_id", type=int)
    instagram_create.add_argument("--confirm-reviewed", action="store_true")
    instagram_status = sub.add_parser("instagram-reel-status", help="Consulta status do container Reel")
    instagram_status.add_argument("queue_id", type=int)
    instagram_publish = sub.add_parser(
        "instagram-reel-publish",
        help="Valida a barreira de compliance; media_publish afiliado permanece bloqueado",
    )
    instagram_publish.add_argument("queue_id", type=int)
    instagram_publish.add_argument("--confirm-reviewed", action="store_true")
    instagram_reconcile = sub.add_parser("instagram-reel-reconcile", help="Reconcilia somente tentativa legada ambigua")
    instagram_reconcile.add_argument("queue_id", type=int)
    outcome = instagram_reconcile.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--published-media-id")
    outcome.add_argument("--confirmed-not-published", action="store_true")
    instagram_reconcile.add_argument("--note", required=True)

    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings.database_path)
    db.init()
    selected_adapter = getattr(args, "adapter", "shopee")
    pipeline = Pipeline(db, settings, source_adapter=selected_adapter)

    if args.command == "init-db":
        result = {"database": str(settings.database_path), "status": "initialized"}
    elif args.command == "cycle":
        result = _run_recorded(db, "dry" if settings.dry_run else "real", args.adapter,
                               lambda: pipeline.run(args.source, args.campaign, args.adapter))
    elif args.command == "p1-cycle":
        offer_ids = pipeline.ingest_source(args.source, args.adapter)
        result = P1Pipeline(db, settings).run(offer_ids, args.campaign)
    elif args.command == "p1-offer":
        result = P1Pipeline(db, settings).generate_offer(args.offer_id, args.campaign)
    elif args.command == "import-conversions":
        result = {"imported": import_conversion_csv(db, args.source)}
    elif args.command == "scheduler-once":
        result = _run_recorded(db, "dry" if settings.dry_run else "real", args.adapter,
                               lambda: run_tick(db, pipeline, args.source, datetime.now()))
    elif args.command == "events":
        result = [dict(row) for row in db.list_operation_events(args.limit)]
    elif args.command == "telegram-processing":
        result = [dict(row) for row in db.list_processing_telegram_queue(args.limit)]
    elif args.command == "telegram-reconcile":
        result = db.reconcile_telegram_queue(
            args.queue_id,
            published_message_id=args.published_message_id,
            confirmed_not_published=args.confirmed_not_published,
        )
    elif args.command == "import-offers":
        if args.adapter == "admitad":
            if not args.merchant_name:
                parser.error("--merchant-name e obrigatorio para o export Admitad")
            offers = AdmitadFeedAdapter(args.merchant_name, delimiter=args.delimiter).import_offers(args.source)
            result = {
                "adapter": "admitad",
                "offers": pipeline.ingest_offers(offers),
                "status": "PENDING_MERCHANT_REVIEW",
            }
        else:
            result = {"adapter": args.adapter, "offers": pipeline.ingest_source(args.source, args.adapter)}
    elif args.command == "amazon-search":
        raise RuntimeError("Amazon live bloqueada: aguarda aprovacao escrita e desenho de retencao validado")
    elif args.command == "social-queue":
        result = [dict(row) for row in db.list_social_queue()]
    elif args.command == "copy-preview":
        offer = db.get_offer(args.offer_id)
        if offer is None:
            raise ValueError(f"oferta {args.offer_id} nao encontrada")
        result = copy_preview(dict(offer), args.channel, settings)
    elif args.command == "instagram-reel-create":
        result = InstagramReelPublisher(db, settings).create_reel(
            args.queue_id,
            approved=args.confirm_reviewed,
        )
    elif args.command == "instagram-reel-status":
        result = InstagramReelPublisher(db, settings).check_status(args.queue_id)
    elif args.command == "instagram-reel-publish":
        result = InstagramReelPublisher(db, settings).publish_reel(
            args.queue_id,
            approved=args.confirm_reviewed,
        )
    elif args.command == "instagram-reel-reconcile":
        result = InstagramReelPublisher(db, settings).reconcile(
            args.queue_id,
            published_media_id=args.published_media_id,
            confirmed_not_published=args.confirmed_not_published,
            note=args.note,
        )
    else:
        result = db.overview()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
