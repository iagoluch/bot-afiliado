"""Cross-category, table-driven tests for the shared product interpreter.

These are representative titles, not HTML scraped from the user's session.
Live checkout DOM remains unverified until inspected locally on the Acer.
"""
from __future__ import annotations

import pytest

from app.services.product_profile import interpret_product
from app.services.tech_drops import creative_copy
from app.services.content import telegram_direct_message


# Primary product vs compatible target: one consistent algorithm for all.
TITLES = [
    ("Fone de ouvido TWS Philips TAT1109BK/00 preto IPX4", "headphones"),
    ("Headphone Dapon H02D Bluetooth 5.1 Over-ear 22h", "headphones"),
    ("Headset gamer RGB para Playstation 5", "headphones"),
    ("Carregador Turbo 30W para iPhone 15 16 17 Pro Max com Cabo", "charger"),
    ("Carregador USB-C 65W para Notebook Acer", "charger"),
    ("Cabo USB-C reforçado para iPhone 16", "cable"),
    ("Cabo HDMI 2.1 2m para Monitor Gamer", "cable"),
    ("Capa transparente para Samsung Galaxy S24", "case"),
    ("Capinha de silicone para iPad", "case"),
    ("Película de vidro 9H para iPhone 15", "film"),
    ("Adaptador USB-C para HDMI Notebook", "adapter"),
    ("Hub USB-C de 7 portas para Macbook", "adapter"),
    ("Suporte articulado para Monitor 27 polegadas", "stand"),
    ("Power Bank USB-C 20000mAh para Celular", "powerbank"),
    ("SSD Kingston NVMe 1TB para Notebook", "ssd"),
    ("SSD M.2 PCIe 4.0 2TB", "ssd"),
    ("HD externo 2TB USB 3.0", "hdd"),
    ("Memória RAM DDR4 16GB para Notebook", "memory"),
    ("Placa-mãe Asus AM4 DDR4 com Wi-Fi", "motherboard"),
    ("Placa de vídeo GeForce RTX 4060 8GB", "graphics"),
    ("Processador AMD Ryzen 5 4500 AM4", "cpu"),
    ("Intel Core i5 12400 processador", "cpu"),
    ("Fonte ATX 650W 80 Plus para PC", "psu"),
    ("Gabinete Gamer Aquário com Vidro Frontal", "pc_case"),
    ("Monitor Gamer Dell 25 IPS 320Hz Full HD", "monitor"),
    ("Webcam Full HD para Notebook", "webcam"),
    ("Microfone USB para Notebook Gamer", "microphone"),
    ("Caixa de Som Bluetooth 5.3 para PC", "speaker"),
    ("Teclado Bluetooth para iPad 11", "keyboard"),
    ("Mouse sem fio para Notebook", "mouse"),
    ("Tablet VAIO TL12 8GB RAM 256GB AMOLED com Teclado", "tablet"),
    ("Notebook Lenovo 15 polegadas 8GB RAM", "notebook"),
    ("Macbook Air M3 16GB", "notebook"),
    ("Celular iPhone 15 128GB com cabo", "phone"),
    ("Smartphone Galaxy A55 256GB com carregador", "phone"),
    ("Smartwatch Bluetooth para iPhone", "watch"),
    ("Roteador Wi-Fi 6 para notebook e celular", "router"),
    ("Impressora Multifuncional Wi-Fi", "printer"),
    ("Controle Bluetooth para Xbox Series", "controller"),
    ("Console Playstation 5 Slim", "console"),
    ("Pendrive Kingston USB-C 128GB", "pendrive"),
    ("Kit Gamer Teclado Mouse Headset", "kit"),
    ("Organizador de cabos para setup", "unknown"),
    ("Produto Eletrônico XYZ Genérico", "unknown"),
]


@pytest.mark.parametrize(("title", "category"), TITLES)
def test_product_interpreter_primary_identity(title, category):
    product = interpret_product(title)
    assert product.category.code == category
    assert product.confidence == ("UNKNOWN" if category == "unknown" else "TITLE_MATCH")
    heading, message = creative_copy(title, title)
    assert heading.startswith(product.category.emoji)
    assert "#publi" not in message
    assert "Mais uma opção de tecnologia" not in message
    assert "Pensando em trocar de celular" not in message
    if product.features:
        for feature in product.features:
            assert feature in title
            assert feature in message
    else:
        assert message == ""


@pytest.mark.parametrize(("title", "required"), [
    ("Fone de ouvido TWS Philips TAT1109BK/00 IPX4", ("TWS", "IPX4")),
    ("Carregador Turbo 30W USB-C", ("30W", "USB-C")),
    ("Monitor IPS Gamer 320Hz 25 polegadas", ("IPS", "320Hz", "25 polegadas")),
    ("Tablet VAIO TL12 8GB RAM 256GB AMOLED", ("8GB RAM", "256GB", "AMOLED")),
    ("SSD NVMe 1TB", ("NVMe", "1TB")),
    ("Notebook DDR4 8GB RAM com Wi-Fi 6", ("DDR4", "8GB RAM", "Wi-Fi 6")),
])
def test_technical_facts_are_copied_from_title_only(title, required):
    product = interpret_product(title)
    assert set(required).issubset(set(product.features))


def test_brand_in_compatibility_does_not_determine_product():
    accessory = interpret_product("Carregador 30W USB-C para iPhone 15 com cabo")
    assert accessory.category.code == "charger"
    assert "iPhone" not in accessory.category.aliases
    actual_phone = interpret_product("Celular iPhone 15 128GB com carregador")
    assert actual_phone.category.code == "phone"


def test_unknown_product_does_not_get_invented_classification_or_ai_hook():
    heading, hook = creative_copy("Gadget XYZ Edição Especial", "id", "Perfeito smartphone com bateria premium")
    assert heading == "🔎 TECH DROP NA ÁREA!"
    assert hook == ""


def test_bad_ai_device_or_unverified_feature_is_not_published():
    title = "Carregador 30W para iPhone 15"
    for prompt in (
        "Pensando em trocar de celular? Compare este iPhone.",
        "Esse carregador possui bateria durável e tem qualidade premium.",
        "O melhor carregador original com garantia da loja.",
    ):
        _, line = creative_copy(title, "id", prompt)
        assert prompt not in line
    _, safe = creative_copy(title, "id", "Esse carregador merece entrar na lista de comparação.")
    assert safe == "Esse carregador merece entrar na lista de comparação."


def test_publication_uses_facts_without_unverified_card_or_payment_prices():
    offer = {
        "title": "Fone de ouvido TWS Philips TAT1109BK/00 preto IPX4",
        "external_product_id": "MLBexample",
        "merchant": "Mercado Livre",
        "source_type": "ML_BROWSER_VERIFIED",
        "affiliate_url": "https://meli.la/2MWiFPG",
        "current_price_cents": 12900,
        "tracking_metadata": {
            "price_evidence": "VISIBLE_DOM_PRICE_V2",
            "price_payment_method": "STANDARD",
            "shipping_evidence": "VISIBLE_DOM_SHIPPING_V1",
            "shipping_free": True, "shipping_tomorrow": True, "shipping_full": True,
        },
    }
    body = telegram_direct_message(offer)
    assert "FONE DE OUVIDO" in body
    assert "TWS" in body and "IPX4" in body
    assert "💰 <b>R$ 129,00</b>" in body
    assert "Parcelamento:" not in body
    assert "Cartão: consulte" not in body
    assert "no Pix" not in body
    assert "Entrega grátis" in body
    assert "FULL anunciado" in body
    assert body.endswith("https://meli.la/2MWiFPG\n\n#publi")
