from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.admitad_feed import AdmitadFeedAdapter
from app.adapters.base import Capability, CapabilityStatus


HEADER = "article,vendorCode,name,price,currencyID,url,picture,categoryId,oldprice\n"
ROW = (
    '123,,Cafeteira,"99,90",BRL,'
    'https://ad.admitad.com/g/abc123/?ulp=https%3A%2F%2Floja.example%2Fp%2F123&subid=canal-1,'
    'https://img.example/cafe.jpg,55,"149,90"\n'
)


def write_feed(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "admitad.csv"
    path.write_text(body, encoding="utf-8")
    return path


def test_normalizes_only_explicit_template_and_preserves_subid(tmp_path: Path) -> None:
    adapter = AdmitadFeedAdapter("Loja A")
    offer = adapter.import_offers(write_feed(tmp_path, HEADER + ROW))[0]

    assert adapter.integration_status == CapabilityStatus.MANUAL_OR_PENDING
    assert adapter.validation_status == "WAITING_FOR_REAL_EXPORT"
    assert adapter.capabilities[Capability.CREATE_AFFILIATE_LINK] == CapabilityStatus.MANUAL_OR_PENDING
    assert offer.merchant == "Loja A"
    assert offer.external_product_id == "123"
    assert offer.current_price_cents == 9990
    assert offer.affiliate_url == "https://ad.admitad.com/g/abc123/?ulp=https%3A%2F%2Floja.example%2Fp%2F123&subid=canal-1"
    assert offer.deeplink == offer.affiliate_url
    assert offer.original_price_cents is None
    assert offer.discount_percent is None
    assert offer.tracking_metadata["validation_status"] == "WAITING_FOR_REAL_EXPORT"
    assert offer.image_urls == ["https://img.example/cafe.jpg"]


def test_accepts_shortlink_vendor_code_and_explicit_renamed_columns(tmp_path: Path) -> None:
    adapter = AdmitadFeedAdapter(
        "Loja B", columns={"vendorCode": "sku", "name": "produto", "currencyID": "moeda", "url": "link"}
    )
    path = write_feed(
        tmp_path,
        "sku,produto,price,moeda,link\n"
        "sku-1,Produto,10.00,BRL,https://fas.st/GOKTt?subid=abc\n",
    )
    offer = adapter.import_offers(path)[0]
    assert offer.external_product_id == "sku-1"
    assert offer.affiliate_url == "https://fas.st/GOKTt?subid=abc"


@pytest.mark.parametrize(
    "url",
    [
        "https://loja.example/p/123",
        "http://ad.admitad.com/g/abc",
        "https://evil-ad.admitad.com/g/abc",
        "https://ad.admitad.com.evil.example/g/abc",
        "https://ad.admitad.com@evil.example/g/abc",
        "https://ad.admitad.com/other/abc",
        "https://fas.st/",
        "https://ad.admitad.com:443/g/abc",
    ],
)
def test_rejects_non_affiliate_or_unsafe_url(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError, match="url"):
        AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, HEADER + ROW.replace("https://ad.admitad.com/g/abc123/?ulp=https%3A%2F%2Floja.example%2Fp%2F123&subid=canal-1", url)))


def test_rejects_non_brl_and_invalid_price(tmp_path: Path) -> None:
    for row, match in ((ROW.replace(",BRL,", ",USD,"), "moeda"), (ROW.replace('"99,90"', "0"), "positivo")):
        with pytest.raises(ValueError, match=match):
            AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, HEADER + row))


def test_rejects_missing_product_identifier_and_limits(tmp_path: Path) -> None:
    adapter = AdmitadFeedAdapter("Loja", max_file_bytes=200, max_rows=1)
    with pytest.raises(ValueError, match="bytes"):
        adapter.import_offers(write_feed(tmp_path, HEADER + ROW))
    with pytest.raises(ValueError, match="linhas"):
        AdmitadFeedAdapter("Loja", max_rows=1).import_offers(write_feed(tmp_path, HEADER + ROW + ROW))
    with pytest.raises(ValueError, match="article ou vendorCode"):
        AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, "name,price,currencyID,url\nP,1,BRL,https://fas.st/a\n"))
    with pytest.raises(ValueError, match="campo CSV invalido"):
        huge = f"1,,{'X' * 65_000},1,BRL,https://fas.st/a,,,\n"
        AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, HEADER + huge))


def test_rejects_malformed_headers_and_rows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicadas"):
        AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, "article,name,name,price,currencyID,url\n1,a,a,1,BRL,https://fas.st/a\n"))
    with pytest.raises(ValueError, match="estrutura"):
        AdmitadFeedAdapter("Loja").import_offers(write_feed(tmp_path, HEADER + ROW + ",extra\n"))
