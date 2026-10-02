from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.models import Offer, normalize_utc_timestamp, utc_now


MAX_CONTENT_JOB_ATTEMPTS = 5


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant TEXT NOT NULL,
    affiliate_network TEXT NOT NULL,
    external_product_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    original_price_cents INTEGER,
    current_price_cents INTEGER NOT NULL CHECK(current_price_cents >= 0),
    discount_percent REAL,
    coupon TEXT,
    coupon_expiration TEXT,
    shipping TEXT,
    rating REAL,
    sales_count INTEGER,
    commission_rate REAL,
    commission_estimate_cents INTEGER,
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    source_url TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    deeplink TEXT,
    collected_at TEXT NOT NULL,
    expires_at TEXT,
    stock_status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    tracking_metadata_json TEXT NOT NULL DEFAULT '{}',
    content_fingerprint TEXT NOT NULL DEFAULT '',
    deal_score REAL,
    score_class TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(affiliate_network, merchant, external_product_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    price_cents INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(offer_id, price_cents, observed_at)
);

CREATE TABLE IF NOT EXISTS content_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'text',
    payload_json TEXT NOT NULL DEFAULT '{}',
    assets_json TEXT NOT NULL DEFAULT '[]',
    campaign_id TEXT NOT NULL DEFAULT 'organic',
    content_key TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    content_package_id INTEGER NOT NULL UNIQUE REFERENCES content_packages(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('READY_FOR_PUBLISH','ASSET_PENDING','PENDING_POLICY_REVIEW','PENDING_MERCHANT_REVIEW','PUBLISHED','CANCELLED')),
    required_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instagram_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    social_queue_id INTEGER NOT NULL UNIQUE REFERENCES social_queue(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL CHECK(media_type='REELS'),
    asset_url TEXT NOT NULL,
    container_id TEXT NOT NULL,
    media_id TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'CONTAINER_CREATED','PROCESSING','READY_TO_PUBLISH','PROCESSING_FAILED',
        'PUBLISHING','PUBLISH_AMBIGUOUS','PUBLISHED'
    )),
    error_code TEXT,
    reconciliation_note TEXT,
    reconciled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    content_package_id INTEGER NOT NULL REFERENCES content_packages(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    affiliate_url_snapshot TEXT NOT NULL,
    dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','SIMULATED','PUBLISHED','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES publish_queue(id),
    offer_id INTEGER NOT NULL REFERENCES offers(id),
    channel TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    dry_run INTEGER NOT NULL,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clicks (
    click_id TEXT PRIMARY KEY,
    offer_id INTEGER NOT NULL REFERENCES offers(id),
    merchant TEXT NOT NULL,
    channel TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'unknown',
    timestamp TEXT NOT NULL,
    referrer TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    sub_id TEXT
);

CREATE TABLE IF NOT EXISTS impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id),
    merchant TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    format TEXT NOT NULL,
    creative_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    verified_real INTEGER NOT NULL CHECK(verified_real=1)
);

CREATE TABLE IF NOT EXISTS conversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_order_id TEXT NOT NULL,
    offer_id INTEGER REFERENCES offers(id),
    click_id TEXT REFERENCES clicks(click_id),
    merchant TEXT NOT NULL,
    network TEXT NOT NULL,
    value_cents INTEGER NOT NULL,
    commission_cents INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','PAID')),
    channel TEXT,
    campaign TEXT,
    timestamp TEXT NOT NULL,
    UNIQUE(network, external_order_id)
);

CREATE TABLE IF NOT EXISTS channel_state (
    channel TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    open_until TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    slot_key TEXT PRIMARY KEY,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    mode TEXT NOT NULL,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL,
    queue_id INTEGER,
    offers INTEGER,
    queued INTEGER,
    processed INTEGER,
    error_type TEXT
);

CREATE TABLE IF NOT EXISTS worker_runtime (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    status TEXT NOT NULL,
    pid INTEGER,
    started_at TEXT,
    heartbeat_at TEXT NOT NULL,
    last_tick_at TEXT,
    last_cycle_at TEXT,
    last_result TEXT,
    error_type TEXT
);

CREATE TABLE IF NOT EXISTS content_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL,
    facts_fingerprint TEXT NOT NULL,
    dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','COMPLETED','FAILED','SUPERSEDED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    error_type TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(offer_id, campaign_id, facts_fingerprint, dry_run)
);

CREATE INDEX IF NOT EXISTS idx_queue_due ON publish_queue(status, available_at);
CREATE INDEX IF NOT EXISTS idx_publications_offer_time ON publications(offer_id, published_at);
CREATE INDEX IF NOT EXISTS idx_clicks_offer ON clicks(offer_id);
CREATE INDEX IF NOT EXISTS idx_conversions_offer ON conversions(offer_id);
CREATE INDEX IF NOT EXISTS idx_social_queue_status ON social_queue(status, channel);
CREATE INDEX IF NOT EXISTS idx_instagram_publication_status ON instagram_publications(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_conversions_analytics ON conversions(status,channel,campaign,timestamp);
CREATE INDEX IF NOT EXISTS idx_conversions_click_status ON conversions(click_id,status);
CREATE INDEX IF NOT EXISTS idx_offers_analytics ON offers(category,merchant);
CREATE INDEX IF NOT EXISTS idx_impressions_analytics ON impressions(channel,campaign_id,format,creative_id,timestamp);
CREATE INDEX IF NOT EXISTS idx_content_jobs_due ON content_jobs(status,available_at,id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(publish_queue)")}
            if "dry_run" not in columns:
                # Filas legadas nao registravam o modo. O default seguro impede
                # que uma linha ambigua seja enviada externamente apos upgrade.
                connection.execute("ALTER TABLE publish_queue ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 1")
            if "affiliate_url_snapshot" not in columns:
                connection.execute("ALTER TABLE publish_queue ADD COLUMN affiliate_url_snapshot TEXT")
                connection.execute(
                    """UPDATE publish_queue SET affiliate_url_snapshot=(
                           SELECT affiliate_url FROM offers WHERE offers.id=publish_queue.offer_id
                       ) WHERE affiliate_url_snapshot IS NULL"""
                )
            content_columns = {row["name"] for row in connection.execute("PRAGMA table_info(content_packages)")}
            content_migrations = {
                "format": "ALTER TABLE content_packages ADD COLUMN format TEXT NOT NULL DEFAULT 'text'",
                "payload_json": "ALTER TABLE content_packages ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'",
                "assets_json": "ALTER TABLE content_packages ADD COLUMN assets_json TEXT NOT NULL DEFAULT '[]'",
                "campaign_id": "ALTER TABLE content_packages ADD COLUMN campaign_id TEXT NOT NULL DEFAULT 'organic'",
                "content_key": "ALTER TABLE content_packages ADD COLUMN content_key TEXT",
            }
            for name, statement in content_migrations.items():
                if name not in content_columns:
                    connection.execute(statement)
            offer_columns = {row["name"] for row in connection.execute("PRAGMA table_info(offers)")}
            if "content_fingerprint" not in offer_columns:
                connection.execute("ALTER TABLE offers ADD COLUMN content_fingerprint TEXT NOT NULL DEFAULT ''")
            click_columns = {row["name"] for row in connection.execute("PRAGMA table_info(clicks)")}
            if "format" not in click_columns:
                connection.execute("ALTER TABLE clicks ADD COLUMN format TEXT NOT NULL DEFAULT 'unknown'")
            social_sql = str(connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='social_queue'").fetchone()["sql"])
            if "PENDING_MERCHANT_REVIEW" not in social_sql:
                connection.executescript("""
                    ALTER TABLE social_queue RENAME TO social_queue_legacy;
                    CREATE TABLE social_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
                        content_package_id INTEGER NOT NULL UNIQUE REFERENCES content_packages(id) ON DELETE CASCADE,
                        channel TEXT NOT NULL,
                        format TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('READY_FOR_PUBLISH','ASSET_PENDING','PENDING_POLICY_REVIEW','PENDING_MERCHANT_REVIEW','PUBLISHED','CANCELLED')),
                        required_action TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO social_queue SELECT * FROM social_queue_legacy;
                    DROP TABLE social_queue_legacy;
                """)
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_content_key ON content_packages(content_key) WHERE content_key IS NOT NULL")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_content_analytics ON content_packages(channel,format,campaign_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_queue_mode_due ON publish_queue(dry_run,status,available_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_social_queue_status ON social_queue(status,channel)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_clicks_analytics ON clicks(channel,campaign_id,format,creative_id,timestamp)")
            # Versões anteriores publicavam o preço riscado do CSV manual sem
            # histórico independente. Invalide rascunhos que possam conter a
            # alegação antes de limpar os campos legados da oferta.
            legacy_claims = """SELECT id FROM offers WHERE source_type='MANUAL_OFFICIAL_LINK'
                AND (original_price_cents IS NOT NULL OR discount_percent IS NOT NULL)"""
            connection.execute(
                f"DELETE FROM publish_queue WHERE status IN ('PENDING','FAILED') AND offer_id IN ({legacy_claims})"
            )
            connection.execute(
                f"""UPDATE social_queue SET status='CANCELLED',
                    required_action='Rascunho cancelado: preco anterior nao verificado; gere novamente.',
                    updated_at=?
                    WHERE status NOT IN ('PUBLISHED','CANCELLED') AND offer_id IN ({legacy_claims})""",
                (utc_now(),),
            )
            connection.execute(
                f"UPDATE offers SET original_price_cents=NULL, discount_percent=NULL WHERE id IN ({legacy_claims})"
            )
            connection.commit()
            connection.execute("PRAGMA journal_mode = WAL")

    def upsert_offer(
        self,
        offer: Offer,
        *,
        content_campaign_id: str | None = None,
        content_dry_run: bool | None = None,
    ) -> int:
        for field in ("merchant", "affiliate_network", "external_product_id", "title"):
            if not str(getattr(offer, field) or "").strip():
                raise ValueError(f"{field} da oferta e obrigatorio")
        now = utc_now()
        offer_values = {field: getattr(offer, field) for field in (
                "merchant", "affiliate_network", "external_product_id", "title", "description",
                "category", "brand", "original_price_cents", "current_price_cents", "discount_percent",
                "coupon", "coupon_expiration", "shipping", "rating", "sales_count", "commission_rate",
                "commission_estimate_cents", "source_url", "affiliate_url", "deeplink", "collected_at",
                "expires_at", "stock_status", "source_type"
            )}
        offer_values["collected_at"] = normalize_utc_timestamp(
            str(offer_values["collected_at"]),
            field="collected_at",
        )
        for field, label in (
            ("expires_at", "expires_at"),
            ("coupon_expiration", "coupon_expiration"),
        ):
            if offer_values[field]:
                offer_values[field] = normalize_utc_timestamp(
                    str(offer_values[field]),
                    field=label,
                )
        image_urls_json = json.dumps(offer.image_urls, ensure_ascii=False)
        tracking_metadata_json = json.dumps(offer.tracking_metadata, ensure_ascii=False, sort_keys=True)
        fingerprint_values = {
            key: value for key, value in offer_values.items() if key != "collected_at"
        }
        fingerprint_values.update({
            "image_urls_json": image_urls_json,
            "tracking_metadata_json": tracking_metadata_json,
        })
        content_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        values = {
            **offer_values,
            "image_urls_json": image_urls_json,
            "tracking_metadata_json": tracking_metadata_json,
            "content_fingerprint": content_fingerprint,
            "updated_at": now,
        }
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in values
            if name not in {"affiliate_network", "merchant", "external_product_id"}
        )
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO offers ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(affiliate_network, merchant, external_product_id) DO UPDATE SET {updates}",
                values,
            )
            row = connection.execute(
                "SELECT id FROM offers WHERE affiliate_network=? AND merchant=? AND external_product_id=?",
                (offer.affiliate_network, offer.merchant, offer.external_product_id),
            ).fetchone()
            offer_id = int(row["id"])
            connection.execute(
                "INSERT OR IGNORE INTO price_history(offer_id, price_cents, observed_at) VALUES(?,?,?)",
                (offer_id, offer.current_price_cents, offer_values["collected_at"]),
            )
            if content_campaign_id is not None:
                campaign_id = content_campaign_id.strip()
                if not campaign_id:
                    raise ValueError("campaign_id do job de conteudo e obrigatorio")
                if content_dry_run is None:
                    raise ValueError("modo do job de conteudo e obrigatorio")
                existing = connection.execute(
                    """SELECT id FROM content_jobs
                       WHERE offer_id=? AND facts_fingerprint=? AND dry_run=? AND status!='SUPERSEDED'
                       LIMIT 1""",
                    (offer_id, content_fingerprint, int(content_dry_run)),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """INSERT INTO content_jobs(
                               offer_id,campaign_id,facts_fingerprint,dry_run,status,
                               available_at,created_at,updated_at
                           ) VALUES(?,?,?,?,'PENDING',?,?,?)
                           ON CONFLICT(offer_id,campaign_id,facts_fingerprint,dry_run) DO UPDATE SET
                               status='PENDING',attempts=0,available_at=excluded.available_at,
                               error_type=NULL,updated_at=excluded.updated_at
                           WHERE content_jobs.status='SUPERSEDED'""",
                        (offer_id, campaign_id, content_fingerprint, int(content_dry_run), now, now, now),
                    )
        return offer_id

    def get_offer(self, offer_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()

    def list_offers(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM offers ORDER BY updated_at DESC LIMIT ?", (limit,)))

    def search_offers(self, query: str = "", limit: int = 100) -> list[sqlite3.Row]:
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT * FROM offers WHERE ?='' OR title LIKE ? ESCAPE '\\' COLLATE NOCASE OR category LIKE ? ESCAPE '\\' COLLATE NOCASE OR merchant LIKE ? ESCAPE '\\' COLLATE NOCASE ORDER BY updated_at DESC LIMIT ?",
                (query.strip(), pattern, pattern, pattern, limit),
            ))

    def price_stats(self, offer_id: int) -> tuple[int | None, int]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MIN(price_cents) AS minimum, COUNT(*) AS samples FROM price_history WHERE offer_id=?",
                (offer_id,),
            ).fetchone()
        return row["minimum"], row["samples"]

    def publication_count(self, offer_id: int) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM publications WHERE offer_id=? AND dry_run=0", (offer_id,)).fetchone()[0])

    def set_score(self, offer_id: int, score: float, classification: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE offers SET deal_score=?, score_class=? WHERE id=?", (score, classification, offer_id))

    def add_content(
        self,
        offer_id: int,
        channel: str,
        body: str,
        *,
        format: str = "text",
        payload: dict[str, Any] | None = None,
        assets: list[str] | None = None,
        campaign_id: str = "organic",
        content_key: str | None = None,
    ) -> int:
        with self.connect() as connection:
            if content_key:
                row = connection.execute(
                    """SELECT c.id,
                              (
                                  EXISTS(
                                      SELECT 1 FROM social_queue s
                                      WHERE s.content_package_id=c.id
                                        AND s.status IN ('PUBLISHED','CANCELLED')
                                  )
                                  OR EXISTS(
                                      SELECT 1 FROM publish_queue q
                                      WHERE q.content_package_id=c.id
                                        AND q.status IN ('PUBLISHED','SIMULATED')
                                  )
                              ) terminal
                       FROM content_packages c
                       WHERE c.content_key=?""",
                    (content_key,),
                ).fetchone()
                if row:
                    if not bool(row["terminal"]):
                        connection.execute(
                            "UPDATE content_packages SET body=?,format=?,payload_json=?,assets_json=?,campaign_id=? WHERE id=?",
                            (body, format, json.dumps(payload or {}, ensure_ascii=False), json.dumps(assets or [], ensure_ascii=False), campaign_id, row["id"]),
                        )
                    return int(row["id"])
            cursor = connection.execute(
                "INSERT INTO content_packages(offer_id,channel,body,format,payload_json,assets_json,campaign_id,content_key,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (offer_id, channel, body, format, json.dumps(payload or {}, ensure_ascii=False), json.dumps(assets or [], ensure_ascii=False), campaign_id, content_key, utc_now()),
            )
            row = {"id": cursor.lastrowid}
            return int(row["id"])

    def enqueue_social(
        self,
        offer_id: int,
        content_id: int,
        channel: str,
        format: str,
        status: str,
        required_action: str | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO social_queue(
                       offer_id,content_package_id,channel,format,status,required_action,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(content_package_id) DO UPDATE SET
                       status=excluded.status,
                       required_action=excluded.required_action,
                       updated_at=excluded.updated_at
                   WHERE social_queue.status NOT IN ('PUBLISHED','CANCELLED')""",
                (offer_id, content_id, channel, format, status, required_action, now, now),
            )
            row = connection.execute("SELECT id FROM social_queue WHERE content_package_id=?", (content_id,)).fetchone()
            return int(row["id"])

    def list_social_queue(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT q.*,c.body,c.payload_json,c.assets_json,o.title FROM social_queue q JOIN content_packages c ON c.id=q.content_package_id JOIN offers o ON o.id=q.offer_id ORDER BY q.id DESC LIMIT ?",
                (limit,),
            ))

    def get_social_queue(self, queue_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT q.*,c.body,c.payload_json,c.assets_json,c.campaign_id,
                          o.title,o.merchant,o.affiliate_network,o.source_url,o.affiliate_url,
                          o.current_price_cents,o.original_price_cents,o.coupon,o.coupon_expiration,
                          o.stock_status,o.expires_at
                   FROM social_queue q
                   JOIN content_packages c ON c.id=q.content_package_id
                   JOIN offers o ON o.id=q.offer_id
                   WHERE q.id=?""",
                (queue_id,),
            ).fetchone()

    def instagram_publication(self, queue_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def create_instagram_publication(self, queue_id: int, asset_url: str, container_id: str) -> sqlite3.Row:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO instagram_publications(
                       social_queue_id,media_type,asset_url,container_id,status,created_at,updated_at
                   ) VALUES(?,'REELS',?,?,'CONTAINER_CREATED',?,?)""",
                (queue_id, asset_url, container_id, now, now),
            )
            connection.execute(
                "UPDATE social_queue SET required_action=?,updated_at=? WHERE id=?",
                ("Container Reel criado; consultar o status ate FINISHED antes de publicar.", now, queue_id),
            )
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def update_instagram_processing(self, queue_id: int, status: str, error_code: str | None = None) -> sqlite3.Row:
        allowed = {"PROCESSING", "READY_TO_PUBLISH", "PROCESSING_FAILED"}
        if status not in allowed:
            raise ValueError(f"estado de processamento Instagram invalido: {status}")
        now = utc_now()
        action = {
            "PROCESSING": "A Meta ainda processa o Reel; consultar novamente depois.",
            "READY_TO_PUBLISH": (
                "Container FINISHED; media_publish afiliado bloqueado até existir contrato oficial "
                "para aplicar o rótulo de parceria paga."
            ),
            "PROCESSING_FAILED": "Container falhou ou expirou; revisar manualmente antes de criar outro.",
        }[status]
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE instagram_publications
                   SET status=?,error_code=?,updated_at=?
                   WHERE social_queue_id=? AND status IN ('CONTAINER_CREATED','PROCESSING','READY_TO_PUBLISH','PROCESSING_FAILED')""",
                (status, error_code, now, queue_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("publicacao Instagram nao permite atualizar o processamento neste estado")
            connection.execute(
                "UPDATE social_queue SET required_action=?,updated_at=? WHERE id=?",
                (action, now, queue_id),
            )
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def claim_instagram_publish(self, queue_id: int) -> sqlite3.Row:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE instagram_publications SET status='PUBLISHING',error_code=NULL,updated_at=?
                   WHERE social_queue_id=? AND status='READY_TO_PUBLISH'""",
                (now, queue_id),
            )
            if cursor.rowcount != 1:
                current = connection.execute(
                    "SELECT status FROM instagram_publications WHERE social_queue_id=?",
                    (queue_id,),
                ).fetchone()
                state = current["status"] if current else "AUSENTE"
                raise ValueError(f"publicacao Instagram nao pode iniciar no estado {state}")
            connection.execute(
                "UPDATE social_queue SET required_action=?,updated_at=? WHERE id=?",
                ("Publicação enviada; qualquer interrupção agora exige reconciliação humana.", now, queue_id),
            )
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def mark_instagram_publish_ambiguous(self, queue_id: int, error_code: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE instagram_publications
                   SET status='PUBLISH_AMBIGUOUS',error_code=?,updated_at=?
                   WHERE social_queue_id=? AND status='PUBLISHING'""",
                (error_code[:100], now, queue_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("publicacao Instagram ambigua nao estava em PUBLISHING")
            connection.execute(
                """UPDATE social_queue SET required_action=?,updated_at=? WHERE id=?""",
                ("PUBLISH_AMBIGUOUS: conferir o perfil no Instagram e reconciliar manualmente; não repetir media_publish.", now, queue_id),
            )

    def mark_instagram_published(self, queue_id: int, media_id: str, *, reconciliation_note: str | None = None) -> sqlite3.Row:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE instagram_publications
                   SET status='PUBLISHED',media_id=?,error_code=NULL,reconciliation_note=?,
                       reconciled_at=CASE WHEN ? IS NULL THEN reconciled_at ELSE ? END,updated_at=?
                   WHERE social_queue_id=? AND status IN ('PUBLISHING','PUBLISH_AMBIGUOUS')""",
                (media_id, reconciliation_note, reconciliation_note, now, now, queue_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("publicacao Instagram nao pode ser concluida neste estado")
            connection.execute(
                "UPDATE social_queue SET status='PUBLISHED',required_action=NULL,updated_at=? WHERE id=?",
                (now, queue_id),
            )
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def reconcile_instagram_not_published(self, queue_id: int, note: str) -> sqlite3.Row:
        if not note.strip():
            raise ValueError("nota de reconciliacao e obrigatoria")
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE instagram_publications
                   SET status='READY_TO_PUBLISH',error_code=NULL,reconciliation_note=?,reconciled_at=?,updated_at=?
                   WHERE social_queue_id=? AND status IN ('PUBLISHING','PUBLISH_AMBIGUOUS')""",
                (note[:500], now, now, queue_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("publicacao Instagram nao exige reconciliacao neste estado")
            connection.execute(
                "UPDATE social_queue SET required_action=?,updated_at=? WHERE id=?",
                (
                    "Ausência da publicação confirmada manualmente; nova chamada media_publish permanece "
                    "bloqueada para conteúdo afiliado.",
                    now,
                    queue_id,
                ),
            )
            return connection.execute(
                "SELECT * FROM instagram_publications WHERE social_queue_id=?",
                (queue_id,),
            ).fetchone()

    def enqueue(self, offer_id: int, content_id: int, channel: str, campaign_id: str, creative_id: str, idempotency_key: str, *, dry_run: bool) -> int:
        now = utc_now()
        with self.connect() as connection:
            offer = connection.execute("SELECT affiliate_url FROM offers WHERE id=?", (offer_id,)).fetchone()
            if offer is None:
                raise ValueError("oferta inexistente para fila")
            connection.execute(
                "INSERT OR IGNORE INTO publish_queue(offer_id,content_package_id,channel,campaign_id,creative_id,idempotency_key,affiliate_url_snapshot,dry_run,status,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'PENDING',?,?,?)",
                (offer_id, content_id, channel, campaign_id, creative_id, idempotency_key, offer["affiliate_url"], int(dry_run), now, now, now),
            )
            row = connection.execute("SELECT id FROM publish_queue WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            return int(row["id"])

    def claim_due(self, *, dry_run: bool) -> sqlite3.Row | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT q.*, c.body, o.affiliate_url, o.merchant FROM publish_queue q JOIN content_packages c ON c.id=q.content_package_id JOIN offers o ON o.id=q.offer_id WHERE q.dry_run=? AND q.status IN ('PENDING','FAILED') AND q.available_at<=? ORDER BY q.id LIMIT 1",
                (int(dry_run), now),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE publish_queue SET status='PROCESSING', attempts=attempts+1, updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                row = connection.execute(
                    "SELECT q.*, c.body, o.affiliate_url, o.merchant FROM publish_queue q JOIN content_packages c ON c.id=q.content_package_id JOIN offers o ON o.id=q.offer_id WHERE q.id=?",
                    (row["id"],),
                ).fetchone()
            return row

    def complete_publication(self, queue: sqlite3.Row, external_message_id: str) -> None:
        now = utc_now()
        dry_run = bool(queue["dry_run"])
        queue_status = "SIMULATED" if dry_run else "PUBLISHED"
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO publications(queue_id,offer_id,channel,campaign_id,creative_id,external_message_id,affiliate_url,idempotency_key,dry_run,published_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (queue["id"], queue["offer_id"], queue["channel"], queue["campaign_id"], queue["creative_id"], external_message_id, queue["affiliate_url_snapshot"], queue["idempotency_key"], int(dry_run), now),
            )
            connection.execute("UPDATE publish_queue SET status=?, last_error=NULL, updated_at=? WHERE id=?", (queue_status, now, queue["id"]))

    def fail_publication(self, queue_id: int, error: str, available_at: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE publish_queue SET status='FAILED', last_error=?, available_at=?, updated_at=? WHERE id=?",
                (error[:500], available_at, utc_now(), queue_id),
            )

    def discard_unpublished_queue(self, queue_id: int, adapter: str, reason_code: str) -> None:
        if reason_code not in {"OFFER_INVALID", "CONTENT_CHANGED"}:
            raise ValueError("motivo de descarte invalido")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            queue = connection.execute(
                "SELECT dry_run FROM publish_queue WHERE id=? AND status='PROCESSING'", (queue_id,)
            ).fetchone()
            if queue is None or connection.execute(
                "SELECT 1 FROM publications WHERE queue_id=?", (queue_id,)
            ).fetchone():
                raise ValueError("fila nao pode ser descartada apos publicacao ou fora de PROCESSING")
            connection.execute("DELETE FROM publish_queue WHERE id=?", (queue_id,))
            connection.execute(
                """INSERT INTO operation_events(created_at,event,mode,adapter,status,queue_id)
                VALUES(?,?,?,?,?,?)""",
                (utc_now(), "queue_rejected", "dry" if queue["dry_run"] else "real", adapter, reason_code, queue_id),
            )
            connection.execute(
                "DELETE FROM operation_events WHERE id <= (SELECT MAX(id)-2000 FROM operation_events)"
            )

    def list_processing_telegram_queue(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """SELECT q.id AS queue_id,q.offer_id,q.campaign_id,q.attempts,q.created_at,q.updated_at,
                          o.merchant,o.title
                   FROM publish_queue q JOIN offers o ON o.id=q.offer_id
                   WHERE q.channel='telegram' AND q.dry_run=0 AND q.status='PROCESSING'
                   ORDER BY q.id LIMIT ?""",
                (max(1, min(int(limit), 200)),),
            ).fetchall()

    def reconcile_telegram_queue(
        self,
        queue_id: int,
        *,
        published_message_id: str | None,
        confirmed_not_published: bool,
    ) -> dict[str, int | str]:
        message_id = published_message_id.strip() if published_message_id else None
        if bool(message_id) == bool(confirmed_not_published):
            raise ValueError("informe message_id publicado ou confirme ausencia, mas nao ambos")
        if message_id and not re.fullmatch(r"[1-9][0-9]{0,19}", message_id):
            raise ValueError("message_id Telegram invalido")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            queue = connection.execute(
                """SELECT q.*,o.affiliate_url FROM publish_queue q
                   JOIN offers o ON o.id=q.offer_id WHERE q.id=?""",
                (queue_id,),
            ).fetchone()
            if queue is None or queue["channel"] != "telegram" or queue["dry_run"] or queue["status"] != "PROCESSING":
                raise ValueError("fila Telegram real nao esta em PROCESSING")
            if connection.execute(
                "SELECT 1 FROM publications WHERE queue_id=? OR idempotency_key=?",
                (queue_id, queue["idempotency_key"]),
            ).fetchone():
                raise ValueError("fila ja possui publicacao registrada")
            now = utc_now()
            if message_id:
                connection.execute(
                    """INSERT INTO publications(queue_id,offer_id,channel,campaign_id,creative_id,
                       external_message_id,affiliate_url,idempotency_key,dry_run,published_at)
                       VALUES(?,?,?,?,?,?,?,?,0,?)""",
                    (queue_id, queue["offer_id"], "telegram", queue["campaign_id"],
                     queue["creative_id"], message_id, queue["affiliate_url_snapshot"], queue["idempotency_key"], now),
                )
                connection.execute(
                    "UPDATE publish_queue SET status='PUBLISHED',last_error=NULL,updated_at=? WHERE id=?",
                    (now, queue_id),
                )
                status = "PUBLISHED"
            else:
                connection.execute("DELETE FROM publish_queue WHERE id=?", (queue_id,))
                status = "NOT_PUBLISHED"
            connection.execute(
                """INSERT INTO operation_events(created_at,event,mode,adapter,status,queue_id)
                   VALUES(?,'queue_reconciled','real','telegram',?,?)""",
                (now, status, queue_id),
            )
            connection.execute("DELETE FROM operation_events WHERE id <= (SELECT MAX(id)-2000 FROM operation_events)")
            result: dict[str, int | str] = {"queue_id": queue_id, "status": status}
            if message_id:
                result["message_id"] = message_id
            return result

    def channel_state(self, channel: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM channel_state WHERE channel=?", (channel,)).fetchone()

    def note_channel_success(self, channel: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO channel_state(channel,consecutive_failures,open_until) VALUES(?,0,NULL) ON CONFLICT(channel) DO UPDATE SET consecutive_failures=0,open_until=NULL",
                (channel,),
            )

    def note_channel_rate_limit(self, channel: str, until: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO channel_state(channel,consecutive_failures,open_until) VALUES(?,0,?) "
                "ON CONFLICT(channel) DO UPDATE SET consecutive_failures=0,open_until=excluded.open_until",
                (channel, until),
            )

    def note_channel_failure(self, channel: str, open_until: str | None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO channel_state(channel,consecutive_failures,open_until) VALUES(?,1,?) ON CONFLICT(channel) DO UPDATE SET consecutive_failures=channel_state.consecutive_failures+1,open_until=excluded.open_until",
                (channel, open_until),
            )

    def claim_schedule_slot(self, slot_key: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO scheduler_runs(slot_key,started_at) VALUES(?,?)",
                (slot_key, utc_now()),
            )
            return cursor.rowcount == 1

    def release_schedule_slot(self, slot_key: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM scheduler_runs WHERE slot_key=?", (slot_key,))

    def record_operation_event(
        self,
        event: str,
        mode: str,
        adapter: str,
        status: str,
        *,
        queue_id: int | None = None,
        offers: int | None = None,
        queued: int | None = None,
        processed: int | None = None,
        error_type: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO operation_events(
                    created_at,event,mode,adapter,status,queue_id,offers,queued,processed,error_type
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (utc_now(), event, mode, adapter, status, queue_id, offers, queued, processed, error_type),
            )
            connection.execute(
                "DELETE FROM operation_events WHERE id <= (SELECT MAX(id)-2000 FROM operation_events)"
            )

    def list_operation_events(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM operation_events ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()

    def record_worker_heartbeat(
        self,
        status: str,
        *,
        pid: int | None,
        started_at: str | None = None,
        last_tick_at: str | None = None,
        last_cycle_at: str | None = None,
        last_result: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Persiste somente metadados operacionais seguros do worker singleton."""
        if status not in {"STARTING", "RUNNING", "ERROR", "STOPPED"}:
            raise ValueError("status de worker invalido")
        if last_result not in {None, "cycle", "queue", "idle", "error", "stopped"}:
            raise ValueError("resultado de worker invalido")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO worker_runtime(
                       id,status,pid,started_at,heartbeat_at,last_tick_at,last_cycle_at,last_result,error_type
                   ) VALUES(1,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       status=excluded.status,
                       pid=excluded.pid,
                       started_at=COALESCE(excluded.started_at,worker_runtime.started_at),
                       heartbeat_at=excluded.heartbeat_at,
                       last_tick_at=COALESCE(excluded.last_tick_at,worker_runtime.last_tick_at),
                       last_cycle_at=COALESCE(excluded.last_cycle_at,worker_runtime.last_cycle_at),
                       last_result=COALESCE(excluded.last_result,worker_runtime.last_result),
                       error_type=excluded.error_type""",
                (
                    status,
                    pid,
                    started_at,
                    now,
                    last_tick_at,
                    last_cycle_at,
                    last_result,
                    error_type,
                ),
            )

    def worker_runtime(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM worker_runtime WHERE id=1").fetchone()

    def recover_content_jobs(self, *, dry_run: bool) -> int:
        """Reabre jobs idempotentes interrompidos por queda do processo."""
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE content_jobs
                   SET status='PENDING',attempts=MAX(attempts-1,0),available_at=?,
                       error_type='WorkerInterrupted',updated_at=?
                   WHERE status='PROCESSING' AND dry_run=?""",
                (now, now, int(dry_run)),
            )
            return int(cursor.rowcount)

    def claim_content_job(self, *, dry_run: bool) -> sqlite3.Row | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT j.*,o.content_fingerprint AS current_fingerprint
                   FROM content_jobs j JOIN offers o ON o.id=j.offer_id
                   WHERE j.dry_run=? AND j.status IN ('PENDING','FAILED')
                     AND j.available_at<=? AND j.attempts<?
                   ORDER BY j.id LIMIT 1""",
                (int(dry_run), now, MAX_CONTENT_JOB_ATTEMPTS),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE content_jobs
                   SET status='PROCESSING',attempts=attempts+1,error_type=NULL,updated_at=?
                   WHERE id=?""",
                (now, row["id"]),
            )
            return connection.execute(
                """SELECT j.*,o.content_fingerprint AS current_fingerprint
                   FROM content_jobs j JOIN offers o ON o.id=j.offer_id WHERE j.id=?""",
                (row["id"],),
            ).fetchone()

    def supersede_content_job(self, job_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE content_jobs
                   SET status='SUPERSEDED',error_type=NULL,updated_at=?
                   WHERE id=? AND status='PROCESSING'""",
                (utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job de conteudo nao esta em processamento")

    def complete_content_job(self, job_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE content_jobs
                   SET status='COMPLETED',error_type=NULL,updated_at=?
                   WHERE id=? AND status='PROCESSING'""",
                (utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job de conteudo nao esta em processamento")

    def fail_content_job(self, job_id: int, error_type: str, available_at: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE content_jobs
                   SET status='FAILED',error_type=?,available_at=?,updated_at=?
                   WHERE id=? AND status='PROCESSING'""",
                (error_type[:100], available_at, utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job de conteudo nao esta em processamento")

    def queue_depths(self, *, dry_run: bool) -> dict[str, int]:
        """Conta separadamente trabalho ativo de publicação, revisão social e conteúdo."""
        with self.connect() as connection:
            publication = connection.execute(
                """SELECT COUNT(*) AS count FROM publish_queue
                   WHERE dry_run=? AND status IN ('PENDING','FAILED','PROCESSING')""",
                (int(dry_run),),
            ).fetchone()
            social = connection.execute(
                """SELECT COUNT(*) AS count FROM social_queue
                   WHERE status IN (
                       'READY_FOR_PUBLISH','ASSET_PENDING',
                       'PENDING_POLICY_REVIEW','PENDING_MERCHANT_REVIEW'
                   )"""
            ).fetchone()
            content = connection.execute(
                """SELECT COUNT(*) AS count FROM content_jobs
                   WHERE dry_run=?
                     AND (
                         status IN ('PENDING','PROCESSING')
                         OR (status='FAILED' AND attempts<?)
                     )""",
                (int(dry_run), MAX_CONTENT_JOB_ATTEMPTS),
            ).fetchone()
            return {
                "publish_queue_depth": int(publication["count"]),
                "social_queue_depth": int(social["count"]),
                "content_job_depth": int(content["count"]),
            }

    def queue_depth(self, *, dry_run: bool) -> int:
        """Compatibilidade: profundidade automática, sem a fila social de revisão manual."""
        depths = self.queue_depths(dry_run=dry_run)
        return depths["publish_queue_depth"] + depths["content_job_depth"]

    def publication_for_key(self, idempotency_key: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM publications WHERE idempotency_key=?", (idempotency_key,)).fetchone()

    def publication_for_tracking_key(
        self, key: str, offer_id: int, channel: str, campaign_id: str, creative_id: str,
    ) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM publications WHERE idempotency_key=? AND offer_id=? AND channel=?
                   AND campaign_id=? AND creative_id=? AND dry_run=0""",
                (key, offer_id, channel, campaign_id, creative_id),
            ).fetchone()

    def record_click(self, offer: sqlite3.Row, *, channel: str, campaign_id: str, creative_id: str, referrer: str | None, utm_source: str | None, utm_medium: str | None, utm_campaign: str | None, sub_id: str | None, format: str = "unknown") -> str:
        click_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO clicks(click_id,offer_id,merchant,channel,campaign_id,creative_id,format,timestamp,referrer,utm_source,utm_medium,utm_campaign,sub_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (click_id, offer["id"], offer["merchant"], channel, campaign_id, creative_id, format, utc_now(), referrer, utm_source, utm_medium, utm_campaign, sub_id),
            )
        return click_id

    def record_impression(self, offer_id: int, *, channel: str, campaign_id: str, format: str, creative_id: str, source: str) -> int:
        offer = self.get_offer(offer_id)
        if offer is None:
            raise ValueError(f"oferta inexistente: {offer_id}")
        if not source.strip():
            raise ValueError("fonte real da impressao e obrigatoria")
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO impressions(offer_id,merchant,category,channel,campaign_id,format,creative_id,timestamp,source,verified_real) VALUES(?,?,?,?,?,?,?,?,?,1)",
                (offer_id, offer["merchant"], offer["category"], channel, campaign_id, format, creative_id, utc_now(), source.strip()),
            )
            return int(cursor.lastrowid)

    def _import_conversion(self, connection: sqlite3.Connection, data: dict[str, Any]) -> None:
        normalized = dict(data)
        normalized["external_order_id"] = str(normalized.get("external_order_id") or "").strip()
        if not normalized["external_order_id"]:
            raise ValueError("ID externo do pedido e obrigatorio")
        normalized["merchant"] = str(normalized.get("merchant") or "").strip()
        normalized["network"] = str(normalized.get("network") or "").strip()
        normalized["timestamp"] = normalize_utc_timestamp(
            str(normalized.get("timestamp") or ""),
            field="timestamp da conversao",
        )
        if not normalized["merchant"] or not normalized["network"]:
            raise ValueError("merchant e network da conversao sao obrigatorios")
        if normalized.get("status") not in {"PENDING", "APPROVED", "REJECTED", "PAID"}:
            raise ValueError("status de conversao invalido")
        for field in ("value_cents", "commission_cents"):
            try:
                normalized[field] = int(normalized.get(field))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} da conversao deve ser inteiro") from exc
            if normalized[field] < 0:
                raise ValueError(f"{field} da conversao nao pode ser negativo")
        if normalized.get("offer_id") is not None:
            normalized["offer_id"] = int(normalized["offer_id"])

        if normalized.get("click_id"):
            click = connection.execute(
                "SELECT offer_id,channel,campaign_id FROM clicks WHERE click_id=?",
                (normalized["click_id"],),
            ).fetchone()
            if click is None:
                raise ValueError("clique da conversao nao encontrado")
            if normalized.get("offer_id") is not None and normalized["offer_id"] != click["offer_id"]:
                raise ValueError("oferta da conversao difere da oferta do clique")
            normalized["offer_id"] = click["offer_id"]
            for field, click_field, label in (
                ("channel", "channel", "canal"),
                ("campaign", "campaign_id", "campanha"),
            ):
                if normalized.get(field) and normalized[field] != click[click_field]:
                    raise ValueError(f"{label} da conversao difere do clique")
                normalized[field] = click[click_field]

        existing = connection.execute(
            """SELECT merchant,offer_id,click_id,channel,campaign
               FROM conversions
               WHERE network=? AND external_order_id=?""",
            (normalized.get("network"), normalized["external_order_id"]),
        ).fetchone()
        if existing is not None:
            if normalized["merchant"] != existing["merchant"]:
                raise ValueError("merchant da conversao difere da importacao original")
            for field in ("offer_id", "click_id", "channel", "campaign"):
                incoming = normalized.get(field)
                if incoming not in (None, "") and incoming != existing[field]:
                    raise ValueError(
                        f"atribuicao da conversao difere da importacao original: {field}"
                    )
                normalized[field] = existing[field]

        connection.execute(
            "INSERT INTO conversions(external_order_id,offer_id,click_id,merchant,network,value_cents,commission_cents,status,channel,campaign,timestamp) VALUES(:external_order_id,:offer_id,:click_id,:merchant,:network,:value_cents,:commission_cents,:status,:channel,:campaign,:timestamp) ON CONFLICT(network,external_order_id) DO UPDATE SET value_cents=excluded.value_cents,commission_cents=excluded.commission_cents,status=excluded.status,timestamp=excluded.timestamp",
            normalized,
        )

    def import_conversion(self, data: dict[str, Any]) -> None:
        with self.connect() as connection:
            self._import_conversion(connection, data)

    def import_conversions(self, rows: Iterable[dict[str, Any]]) -> int:
        imported = 0
        with self.connect() as connection:
            for row in rows:
                self._import_conversion(connection, row)
                imported += 1
        return imported

    def overview(self) -> dict[str, Any]:
        with self.connect() as connection:
            totals = connection.execute("SELECT COUNT(*) offers, COALESCE(SUM(CASE WHEN score_class IN ('EXCELLENT','GOOD') THEN 1 ELSE 0 END),0) approved_offers FROM offers").fetchone()
            clicks = int(connection.execute("SELECT COUNT(*) FROM clicks").fetchone()[0])
            impressions = int(connection.execute("SELECT COUNT(*) FROM impressions WHERE verified_real=1").fetchone()[0])
            publications = int(connection.execute("SELECT COUNT(*) FROM publications WHERE dry_run=0").fetchone()[0])
            simulations = int(connection.execute("SELECT COUNT(*) FROM publications WHERE dry_run=1").fetchone()[0])
            conversions = connection.execute(
                """SELECT COUNT(*) count,
                          COALESCE(SUM(CASE WHEN click_id IS NOT NULL THEN 1 ELSE 0 END),0) attributed_count,
                          COALESCE(SUM(value_cents),0) revenue,
                          COALESCE(SUM(commission_cents),0) commission,
                          COALESCE(SUM(CASE WHEN click_id IS NOT NULL THEN commission_cents ELSE 0 END),0)
                              attributed_commission
                   FROM conversions
                   WHERE status IN ('APPROVED','PAID')"""
            ).fetchone()
            queue = connection.execute("SELECT status, COUNT(*) count FROM publish_queue GROUP BY status").fetchall()
            social_queue = connection.execute("SELECT status, COUNT(*) count FROM social_queue GROUP BY status").fetchall()
            best_offer = connection.execute("SELECT o.id,o.title,COUNT(c.click_id) clicks FROM clicks c JOIN offers o ON o.id=c.offer_id GROUP BY o.id ORDER BY clicks DESC,o.id LIMIT 1").fetchone()
            best_channel = connection.execute("SELECT channel,COUNT(*) clicks FROM clicks GROUP BY channel ORDER BY clicks DESC LIMIT 1").fetchone()
        conversion_count = int(conversions["count"])
        attributed_conversion_count = int(conversions["attributed_count"])
        attributed_commission = int(conversions["attributed_commission"])
        return {
            "offers": int(totals["offers"]),
            "approved_offers": int(totals["approved_offers"]),
            "publications": publications,
            "simulations": simulations,
            "impressions": impressions or None,
            "clicks": clicks,
            "conversions": conversion_count,
            "revenue_cents": int(conversions["revenue"]),
            "commission_cents": int(conversions["commission"]),
            "ctr": round(clicks / impressions * 100, 2) if impressions else None,
            "cvr": round(attributed_conversion_count / clicks * 100, 2) if clicks else 0.0,
            "epc_cents": round(attributed_commission / clicks, 2) if clicks else 0.0,
            "revenue_per_post_cents": round(int(conversions["revenue"]) / publications, 2) if publications else 0.0,
            "commission_per_post_cents": round(int(conversions["commission"]) / publications, 2) if publications else 0.0,
            "queue": {row["status"]: row["count"] for row in queue},
            "social_queue": {row["status"]: row["count"] for row in social_queue},
            "best_offer": dict(best_offer) if best_offer else None,
            "best_channel": dict(best_channel) if best_channel else None,
        }

    def rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, tuple(parameters)))
