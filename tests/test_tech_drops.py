from __future__ import annotations

import io
import urllib.parse

import pytest

from app.services.content import telegram_direct_message
from app.services.tech_drops import clean_title, creative_copy
from app.services.telegram import TelegramClient


def ml_offer(title: str, **changes):
    data = {
        "title": title,
        "external_product_id": "MLB24117280",
        "merchant": "Mercado Livre",
        "source_type": "ML_BROWSER_VERIFIED",
        "current_price_cents": 58900,
        "affiliate_url": "https://meli.la/2MWiFPG",
        "tracking_metadata": {
            "price_evidence": "VISIBLE_DOM_PRICE_V2",
            "price_payment_method": "STANDARD",
        },
    }
    data.update(changes)
    return data


@pytest.mark.parametrize(("title", "word"), [
    ("Processador AMD Ryzen 5 4500", "processador"),
    ("Gabinete Clanm Aquário Vidro Frontal e Lateral", "gabinete"),
    ("SSD NVMe Kingston 1TB", "ssd"),
    ("Notebook Lenovo 15 polegadas", "notebook"),
    ("Mouse gamer sem fio", "mouse"),
    ("Monitor LG 24 polegadas", "monitor"),
    ("Teclado mecânico USB", "teclado"),
    ("Placa de vídeo Radeon", "placa de vídeo"),
    ("Celular Galaxy A55", "celular"),
    ("Memória RAM DDR4 16GB", "memória"),
])
def test_category_copy_is_specific_and_not_generic(title, word):
    text = telegram_direct_message(ml_offer(title))
    assert "#publi" in text
    assert "<b>" in text
    assert word in text.lower()
    assert "Mais um produto para entrar na sua lista" not in text
    assert "Mais uma opção de tecnologia" not in text
    assert "PREÇO CAIU" not in text
    assert text.endswith("https://meli.la/2MWiFPG\n\n#publi")
    assert text == telegram_direct_message(ml_offer(title))


def test_notification_starts_with_creative_hook_and_disclosure_ends_post():
    title = "Fone De Ouvido Headphone Dapon H02D Bluetooth 5.1 Over-ear 22h Preto"
    message = telegram_direct_message(ml_offer(title))
    lines = message.splitlines()
    assert lines[0].startswith("<b>") and lines[0].endswith("</b>")
    assert "FONE" in lines[0] or "ÁUDIO" in lines[0]
    assert "fone" in lines[1].lower() or "headset" in lines[1].lower()
    assert "Dapon H02D" in message
    assert lines[-1] == "#publi"
    assert message.count("#publi") == 1
    assert "<b>R$ 589,00</b>" in message


def test_previous_tech_drops_html_still_sets_parse_mode():
    client = TelegramClient("test-token", "@grupo", dry_run=True)
    assert client._message_params("#publi\n\n<b>OLD</b>")["parse_mode"] == "HTML"
    assert client._message_params("<b>NEW</b>\ncopy\n\n#publi")["parse_mode"] == "HTML"
    assert "parse_mode" not in client._message_params("#publi\nplain text")


def test_tablet_with_ram_and_keyboard_uses_tablet_copy():
    product = "Tablet VAIO TL12 8GB RAM 256GB AMOLED com Teclado"
    body = telegram_direct_message(ml_offer(product))
    assert body.startswith("<b>📲 TABLET") or body.startswith("<b>📱 TABLET")
    assert "tablet" in body.splitlines()[1].lower()
    assert "Montando a configuração do computador" not in body
    assert "MEMÓRIA RAM NA LISTA" not in body


def test_standard_price_does_not_claim_any_pix_terms_without_evidence():
    body = telegram_direct_message(ml_offer("Tablet VAIO TL12 8GB RAM"))
    assert "Pix" not in body
    assert "<b>R$ 589,00</b>" in body
    assert "consulte o parcelamento" not in body


@pytest.mark.parametrize(("title", "correct", "wrong"), [
    (
        "Carregador Turbo Super Rápido 30w Para iPhone 15 16 17 Pro Max - Cabo Blindado Tipo Usb C Dtimp",
        ("CARREGADOR", "ACESSÓRIO"),
        ("SMARTPHONE", "CELULAR"),
    ),
    ("Fone de ouvido Bluetooth para iPhone", ("ÁUDIO", "FONE"), ("SMARTPHONE", "CELULAR")),
    ("Cabo USB-C reforçado para iPhone 16", ("CABO", "CONEXÃO"), ("SMARTPHONE", "CELULAR")),
    ("Capa transparente para iPhone 15", ("CAPA", "PROTEÇÃO"), ("SMARTPHONE", "CELULAR")),
    ("Película de vidro para Galaxy S24", ("PELÍCULA", "PROTEÇÃO"), ("SMARTPHONE", "CELULAR")),
    ("Suporte de notebook ajustável", ("SUPORTE", "ACESSÓRIO"), ("NOTEBOOK",)),
    ("Teclado Bluetooth para iPad", ("TECLADO", "PERIFÉRICO"), ("TABLET",)),
    ("Mouse sem fio para notebook Lenovo", ("MOUSE", "PERIFÉRICO"), ("NOTEBOOK",)),
    ("Carregador 65W para notebook Acer", ("CARREGADOR", "ACESSÓRIO"), ("NOTEBOOK",)),
    ("Webcam Full HD para notebook", ("CÂMERA", "WEBCAM"), ("NOTEBOOK",)),
    ("SSD SATA 1TB para notebook", ("SSD", "ARMAZENAMENTO"), ("NOTEBOOK",)),
    ("Celular iPhone 15 128GB com cabo", ("CELULAR", "SMARTPHONE"), ("CABO",)),
    ("Notebook Lenovo 8GB RAM com fonte USB", ("NOTEBOOK",), ("CARREGADOR",)),
])
def test_primary_product_wins_over_compatibility_device(title, correct, wrong):
    body = telegram_direct_message(ml_offer(title))
    headline, blurb = body.splitlines()[:2]
    assert any(phrase in headline for phrase in correct)
    assert all(phrase not in headline for phrase in wrong)
    assert "#publi" == body.splitlines()[-1]
    assert "https://meli.la/2MWiFPG" in body
    assert "Mais uma opção de tecnologia" not in body


def test_real_charger_regression_no_false_smartphone_or_pix_claim():
    title = (
        "Carregador Turbo Super Rápido 30w Para iPhone 15 16 17 Pro Max "
        "- Cabo Blindado Tipo Usb C Dtimp"
    )
    offer = ml_offer(
        title, current_price_cents=3790,
        tracking_metadata={
            "price_evidence": "VISIBLE_DOM_PRICE_V2",
            "price_payment_method": "STANDARD",
            "shipping_evidence": "VISIBLE_DOM_SHIPPING_V1",
            "shipping_free": False,
            "shipping_tomorrow": True,
            "shipping_full": True,
        },
    )
    body = telegram_direct_message(offer)
    assert "Carregador Turbo" in body
    assert "R$ 37,90" in body
    assert "Pix" not in body
    assert "Pensando em trocar de celular" not in body
    assert "PREÇO CAIU" not in body
    assert "Previsão de entrega para amanhã" in body
    assert "Envio FULL anunciado" in body
    assert "Frete grátis" not in body
    assert "consulte o parcelamento" not in body
    assert body.endswith("https://meli.la/2MWiFPG\n\n#publi")


def test_nemotron_phone_hook_not_allowed_for_charger():
    charger = "Carregador USB-C de 30W para iPhone 15"
    headline, fallback = creative_copy(charger, "real-test", "Troque seu iPhone por outro smartphone.")
    assert "CARREGADOR" in headline or "ACESSÓRIO" in headline
    assert "iPhone" not in fallback and "smartphone" not in fallback
    _, good = creative_copy(charger, "real-test", "Precisa de um carregador para comparar com outros?")
    assert good == "Precisa de um carregador para comparar com outros?"


def test_ryzen_sku_removed_but_valid_specs_kept():
    title = "Processador AMD Ryzen 5 4500 Box AM4 4.1GHz 11MB Cache S/ Vídeo Integrado - 100-100000644BOX"
    text = telegram_direct_message(ml_offer(title))
    assert "100-100000644BOX" not in text
    assert "Ryzen 5 4500" in text
    assert "AM4" in text
    assert len(clean_title(title)) <= 107


def test_ai_generic_is_ignored_and_contextual_ai_is_kept():
    generic = ml_offer("Gabinete Aquário Vidro Frontal", tracking_metadata={
        "price_evidence": "VISIBLE_DOM_PRICE_V2",
        "price_payment_method": "STANDARD",
        "copy_hook": "Mais uma opção de tecnologia para você conferir.",
    })
    text = telegram_direct_message(generic)
    assert "Mais uma opção de tecnologia" not in text
    metadata = dict(generic["tracking_metadata"])
    metadata["copy_hook"] = "Esse gabinete merece entrar na comparação do setup."
    selected = telegram_direct_message({**generic, "tracking_metadata": metadata})
    assert "Esse gabinete merece entrar na comparação do setup." in selected


def test_html_escapes_untrusted_product_and_coupon_data():
    offer = ml_offer("SSD 1TB <fake> & 2TB", coupon="A<&>")
    text = telegram_direct_message(offer)
    assert "&lt;fake&gt;" in text
    assert "&amp;" in text
    assert "A&lt;&amp;&gt;" in text
    assert "<fake>" not in text


def test_verified_payment_terms_are_not_changed_by_copy():
    offer = ml_offer("Gabinete Clanm Aquário", current_price_cents=15130, tracking_metadata={
        "price_evidence": "VISIBLE_DOM_PRICE_V2",
        "price_payment_method": "PIX",
        "other_payment_price_cents": 15180,
        "installment_count": 3,
        "installment_cents": 5060,
        "installment_interest_free": True,
        "installment_with_interest": False,
    })
    body = telegram_direct_message(offer)
    assert "R$ 151,30 no Pix" in body
    assert "R$ 151,80 em outros meios de pagamento" in body
    assert "Parcelamento: 3x de R$ 50,60 sem juros" in body
    assert "Consulte se há parcelamento" not in body


def test_unverified_terms_are_never_invented():
    body = telegram_direct_message(ml_offer("SSD 1TB"))
    assert "sem juros" not in body
    assert "no Pix" not in body
    assert "R$ 589,00" in body
    assert "OFF" not in body
    assert "desconto" not in body.lower()
    assert "consulte o parcelamento" not in body
    assert "Parcelamento: 10x" not in body
    assert "💳" not in body
    assert body.splitlines()[-1] == "#publi"


def test_delivery_copy_is_not_invented_without_evidence():
    offer = ml_offer("Monitor Gamer Alienware 25 polegadas")
    text = telegram_direct_message(offer)
    assert "Entrega grátis" not in text
    assert "amanhã" not in text
    assert "FULL" not in text
    assert "no Pix" not in text


def test_delivery_copy_does_not_trust_unverified_metadata():
    offer = ml_offer("Monitor Gamer Alienware 25 polegadas", tracking_metadata={
        "price_evidence": "VISIBLE_DOM_PRICE_V2",
        "price_payment_method": "STANDARD",
        "shipping_free": True,
        "shipping_tomorrow": True,
        "shipping_full": True,
    })
    text = telegram_direct_message(offer)
    assert "Entrega grátis" not in text
    assert "FULL" not in text
    assert "amanhã" not in text


def test_different_products_can_have_different_headlines():
    a = [creative_copy("SSD NVMe 1TB", f"MLB{i}")[0] for i in range(25)]
    assert len(set(a)) > 1


def test_html_telegram_api_is_opt_in_and_keeps_plain_text_unchanged():
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, urllib.parse.parse_qs(req.data.decode())))
        return io.BytesIO(b'{"ok":true,"result":{"message_id":42}}')

    client = TelegramClient("test-token", "@grupo", dry_run=False, opener=opener,
                            clock=lambda: 0.0, sleep=lambda _: None)
    html_post = telegram_direct_message(ml_offer("SSD NVMe 1TB"))
    assert client.send_message(html_post).message_id == "42"
    assert seen[0][1]["parse_mode"] == ["HTML"]
    assert seen[0][1]["text"] == [html_post]

    client.send_message("#publi\ntexto antigo")
    assert "parse_mode" not in seen[1][1]

    client.send_photo(html_post, "https://http2.mlstatic.com/example.jpg")
    assert seen[2][1]["parse_mode"] == ["HTML"]
    assert seen[2][1]["caption"] == [html_post]
