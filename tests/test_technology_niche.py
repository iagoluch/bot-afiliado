from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import DEFAULT_TECH_OFFER_TERMS, Settings
from app.db import Database
from app.models import Offer, utc_now
from app.services.collection import is_technology_offer, prepare_assisted_offers
from app.services.curation import score_offer
from app.services.pipeline import Pipeline


@pytest.mark.parametrize("title", [
    "PC Gamer Ryzen 5 5600G",
    "SSD NVMe M.2 1TB",
    "Placa-mãe B550 para AMD",
    "Memória DDR5 32 GB",
    "Water Cooler 240mm",
    "Gabinete ATX para computador",
    "Monitor 27 polegadas",
    "Kit Teclado mecânico e Mouse",
    "Headphone Dapon H02D Bluetooth 5.1",
    "Fone Bluetooth TWS",
    "Webcam HD com Microfone USB-C",
    "Notebook Core i5",
    "Raspberry Pi 5",
    "iPhone 16 Pro",
    "Celular Android Samsung",
    "iPad 10ª geração",
    "Tablet 11 polegadas",
    "Capinha para celular",
    "Película para iPhone",
    "Cabo USB-C de 2 metros",
    "Adaptador HDMI DisplayPort",
    "Carregador rápido 45 W",
    "Power Bank 20000mAh",
    "Smartwatch com GPS",
    "Wi-Fi 6 Roteador Mesh",
    "Cabo Ethernet RJ45",
    "Pen Drive 128 GB",
    "Cartão MicroSD 256 GB",
    "Impressora Multifuncional Wi-Fi",
    "Controle Gamer para Playstation",
    "Console Nintendo Switch",
    "Smart TV 4K",
    "Câmera de segurança IP",
    "Tomada inteligente Wi-Fi",
    "Drone com câmera 4K",
])
def test_common_technology_products_accepted(title):
    assert is_technology_offer(
        {"title": title, "category": "", "brand": ""},
        terms=DEFAULT_TECH_OFFER_TERMS,
    ), title


@pytest.mark.parametrize("title", [
    "Conjunto de panelas inox",
    "Camiseta casual masculina",
    "Maquiagem e perfume",
    "Fritadeira elétrica para cozinha",
    "Mesa de jantar de madeira",
    "Cabo de aço inox para varal",
    "Alimento para animais",
    "Jogo de lençol e travesseiro",
    "Brinquedo carrinho infantil",
    "Sabonete hidratante facial",
    "Kit de ferramentas manuais",
])
def test_unrelated_products_rejected(title):
    assert not is_technology_offer(
        {"title": title, "category": "casa e decoracao", "brand": ""},
        terms=DEFAULT_TECH_OFFER_TERMS,
    ), title


def test_punctuation_and_accents_normalize_as_words():
    assert is_technology_offer(
        {"title": "Placa-Mãe B550", "category": "", "brand": ""},
        terms=("placa mae",),
    )
    assert is_technology_offer(
        {"title": "Carregador USB-C", "category": "", "brand": ""},
        terms=("usb c",),
    )
    assert is_technology_offer(
        {"title": "Roteador Wi-Fi 6", "category": "", "brand": ""},
        terms=("wi fi",),
    )
    assert not is_technology_offer(
        {"title": "Mouseira de escritorio", "category": "", "brand": ""},
        terms=("mouse",),
    )


def test_category_can_prove_tech_for_generic_accessory_title():
    assert is_technology_offer(
        {"title": "Kit capa transparente 3 pecas", "category": "Acessorios para Celular", "brand": ""},
        terms=DEFAULT_TECH_OFFER_TERMS,
    )


def test_description_and_links_are_not_proof_of_technology():
    assert not is_technology_offer(
        {
            "title": "Conjunto de panelas",
            "category": "cozinha",
            "brand": "",
            "description": "oferta para compradores de notebook",
            "affiliate_url": "https://meli.la/2MWiFPG",
        },
        terms=DEFAULT_TECH_OFFER_TERMS,
    )


def test_assisted_batch_filters_mixed_catalog_without_inventing_links():
    rows=[]
    for i,(title,category) in enumerate([
        ("Headphone Bluetooth Dapon H02D", "audio"),
        ("Jogo de lençol solteiro", "cama mesa e banho"),
        ("Película protetora iPad", "acessorios tablet"),
    ],start=1):
        rows.append({
            "external_product_id": f"MLB2000000{i}",
            "title": title,
            "current_price": "74.90",
            "category": category,
            "source_url": "https://www.mercadolivre.com.br/p/MLB24117280",
            "affiliate_url": "https://meli.la/2MWiFPG",
            "collected_at": utc_now(),
        })
    batch=prepare_assisted_offers("mercadolivre",rows,terms=DEFAULT_TECH_OFFER_TERMS)
    assert [o.title for o in batch.offers]==[
        "Headphone Bluetooth Dapon H02D","Película protetora iPad",
    ]
    assert batch.rejected_non_technology==1
    assert all(o.affiliate_url=="https://meli.la/2MWiFPG" for o in batch.offers)


def _settings(tmp_path):
    return Settings(
        database_path=tmp_path / "technology.db",
        dry_run=True,
        public_base_url="http://127.0.0.1:8000",
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_only_mode=True,
        technology_offer_terms=DEFAULT_TECH_OFFER_TERMS,
        creatives_path=tmp_path / "media",
    )


def _offer(title, *, external_id):
    return Offer(
        merchant="Mercado Livre",
        affiliate_network="Mercado Livre Afiliados e Criadores",
        external_product_id=external_id,
        title=title,
        category="acessorios" if "panela" in title.lower() else "audio",
        current_price_cents=7490,
        source_url="https://www.mercadolivre.com.br/p/MLB24117280",
        affiliate_url="https://meli.la/2MWiFPG",
        stock_status="IN_STOCK",
        collected_at=utc_now(),
        rating=4.9,
        sales_count=15000,
        commission_rate=15,
        coupon="PROMO10",
        shipping="frete gratis",
    )


def test_queue_rejects_nontech_even_with_high_score(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    pipeline=Pipeline(db,settings)
    nontech=db.upsert_offer(_offer("Kit panelas inox",external_id="MLB90111"))
    score,classification=score_offer(db,nontech)
    assert classification in {"GOOD","EXCELLENT"}
    assert pipeline.curate_and_queue([nontech],"restricted") == []
    assert db.rows("SELECT COUNT(*) AS n FROM publish_queue")[0]["n"]==0


def test_queued_offer_changed_to_nontech_is_rejected_before_sending(tmp_path):
    settings=_settings(tmp_path)
    db=Database(settings.database_path)
    db.init()
    pipeline=Pipeline(db,settings)
    oid=db.upsert_offer(_offer("Headphone Dapon H02D Bluetooth",external_id="MLB90112"))
    assert pipeline.curate_and_queue([oid],"technology") != []
    with db.connect() as connection:
        connection.execute(
            "UPDATE offers SET title=?, category=?, brand=? WHERE id=?",
            ("Kit panelas inox", "casa", "", oid),
        )
    result=pipeline.process_one()
    assert result is not None and result["status"]=="REJECTED_STALE"
    assert result["reason"]=="OFFER_INVALID"
    assert db.rows("SELECT COUNT(*) AS n FROM publications")[0]["n"]==0
