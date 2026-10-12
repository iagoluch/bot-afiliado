from __future__ import annotations

import pytest

from app.adapters.mercadolivre_manual import MercadoLivreManualAdapter

SOURCE = "https://www.mercadolivre.com.br/fone-de-ouvido-headphone-dapon-h02d-bluetooth-51-over-ear-22h-preto/p/MLB24117280?matt_event_ts=1791816874446"
AFFILIATE = "https://meli.la/2MWiFPG"


@pytest.mark.parametrize("valid_source", [
    SOURCE,
    "https://www.mercadolivre.com.br/fone-de-ouvido-headphone-dapon-h02d-bluetooth-51-over-ear-22h-preto/p/MLB24117280",
    "https://www.mercadolivre.com.br/p/MLB24117280",
])
def test_product_source_accepts_official_slugged_p_mlb_url(valid_source):
    MercadoLivreManualAdapter._validate_url(valid_source, "source_url", allow_short=False)


def test_ml_offer_with_user_supplied_product_url_and_affiliate_link():
    from app.models import utc_now
    record = {
        "external_product_id": "MLB4587377807",
        "title": "Fone de Ouvido Dapon H02D Bluetooth 5.1",
        "current_price": "74.90",
        "category": "audio",
        "source_url": SOURCE,
        "affiliate_url": AFFILIATE,
        "stock_status": "IN_STOCK",
        "manual_review_confirmed": True,
        "collected_at": utc_now(),
    }
    offer = MercadoLivreManualAdapter().import_records([record])[0]
    assert offer.source_url == SOURCE
    assert offer.affiliate_url == AFFILIATE
    assert offer.current_price_cents == 7490
    assert offer.tracking_metadata["manual_review_confirmed"] is True


@pytest.mark.parametrize("invalid", [
    "https://evil.example.com.br/fone/p/MLB24117280",
    "https://mercadolivre.com.br.evil.test/p/MLB24117280",
    "http://www.mercadolivre.com.br/fone/p/MLB24117280",
    "https://www.mercadolivre.com.br/fone/p/MLBinvalid",
    "https://www.mercadolivre.com.br/fone/p/MLB24117280-xyz",
    "https://www.mercadolivre.com.br/fone/p/MLB24117280xyz",
])
def test_slugged_mlb_source_fail_closed_on_invalid_urls(invalid):
    with pytest.raises(ValueError, match="source_url deve ser URL HTTPS"):
        MercadoLivreManualAdapter._validate_url(invalid, "source_url", allow_short=False)


def test_short_affiliate_link_still_accepted_but_not_as_source():
    MercadoLivreManualAdapter._validate_url(AFFILIATE, "affiliate_url", allow_short=True)
    with pytest.raises(ValueError, match="source_url"):
        MercadoLivreManualAdapter._validate_url(AFFILIATE, "source_url", allow_short=False)
