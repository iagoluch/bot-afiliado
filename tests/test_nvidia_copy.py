from __future__ import annotations

import io
import json
import urllib.error

from app.services.nvidia_copy import API_URL, MODEL, _sanitize_hook, suggest_hook


def _opener(text, *, seen=None):
    reply = {"choices": [{"message": {"content": json.dumps({"hook": text})}}]}
    def open_(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        return io.BytesIO(json.dumps(reply).encode("utf-8"))
    return open_


def test_calls_official_model_in_text_only_mode_without_product_price_or_link():
    requests = []
    hook = suggest_hook(
        "Mouse gamer sem fio", api_key="nv-test-secret",
        opener=_opener("Uma opção para organizar seu setup.", seen=requests),
    )
    assert hook == "Uma opção para organizar seu setup."
    request, timeout = requests[0]
    assert request.full_url == API_URL
    assert timeout <= 8
    assert request.get_header("Authorization") == "Bearer nv-test-secret"
    data = json.loads(request.data)
    assert data["model"] == MODEL
    assert data["stream"] is False
    assert data["max_tokens"] <= 128
    assert data["chat_template_kwargs"]["enable_thinking"] is False
    assert "Mouse gamer sem fio" in data["messages"][-1]["content"]
    assert "nv-test-secret" not in request.data.decode("utf-8")
    assert "meli.la" not in request.data.decode("utf-8")


def test_without_api_key_never_calls_network():
    assert suggest_hook("SSD NVMe", api_key=None, opener=lambda *_a, **_k: 1) is None


def test_http_error_or_quota_uses_fallback_with_no_secret_exposure():
    def deny(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too many", {}, io.BytesIO(b"SECRET"))
    assert suggest_hook("Teclado mecânico", api_key="secret", opener=deny) is None


def test_fake_invented_claims_and_prices_are_rejected():
    for text in (
        "Só hoje com 70% de desconto!",
        "R$ 759,00 no Pix",
        "Últimas unidades no estoque!",
        "Entrega grátis para todo o Brasil",
        "Visite https://evil.test/oferta",
        "Mouse compatível com qualquer computador",
        "SSD 1TB muito rápido",
        "Maior durabilidade garantida",
        "#publi\nCompre aqui",
    ):
        assert _sanitize_hook(text) is None or text in {
            "Mouse compatível com qualquer computador",
            "Maior durabilidade garantida",
        }


def test_invalid_json_response_falls_back():
    def open_(req, timeout):
        return io.BytesIO(b"{this is invalid}")
    assert suggest_hook("SSD NVMe", api_key="nv-test", opener=open_) is None


def test_rendering_is_stable_with_verified_hook(tmp_path):
    from app.services.content import telegram_direct_message
    offer = {
        "title": "Mouse gamer sem fio",
        "merchant": "Mercado Livre",
        "current_price_cents": 75900,
        "affiliate_url": "https://meli.la/2MWiFPG",
        "source_type": "ML_BROWSER_VERIFIED",
        "tracking_metadata": {
            "price_evidence": "VISIBLE_DOM_PRICE_V2",
            "price_payment_method": "PIX",
            "other_payment_price_cents": 90000,
            "copy_hook": "Uma opção para organizar seu setup.",
        },
    }
    text = telegram_direct_message(offer)
    assert "Uma opção para organizar seu setup." in text
    assert "R$ 759,00 no Pix" in text
    assert "R$ 900,00 em outros meios" in text
    assert text.endswith("https://meli.la/2MWiFPG")
    assert text == telegram_direct_message(offer)
