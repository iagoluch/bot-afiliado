from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
EXT=ROOT/"extensions/ml-chrome/background.js"

def test_real_chrome_price_extractor_ignores_previous_and_labels_pix():
    if shutil.which("node") is None:
        pytest.skip("Node unavailable")
    result=subprocess.run(
        ["node",str(ROOT/"tests/js/test_ml_price_dom.js")],
        cwd=ROOT,capture_output=True,text=True,timeout=15,
    )
    assert result.returncode==0,result.stderr
    assert "ML visible current-price scenarios OK" in result.stdout


def test_product_details_uses_disambiguated_price_not_first_element():
    code=EXT.read_text(encoding="utf-8")
    details=code.split("function productDetails(){",1)[1].split("\nfunction isGenerator(){",1)[0]
    assert "extractVisibleProductPrice()" in details
    assert ".querySelector(" not in details or 'h1.ui-pdp-title' in details
    assert details.index("function extractVisibleProductPrice(){") < details.index("const price=extractVisibleProductPrice()")
    assert "PRODUCT_INSPECTION_ERROR" in details
    assert "buy_now_enabled:true" in details
    assert "VISIBLE_DOM_PRICE_V2" in code
    assert "AMBIGUOUS_PAYMENT_PRICES" in code
    assert "andes-money-amount--previous" in code


def test_product_buybox_validation_with_cart_button_and_installments():
    if shutil.which("node") is None:
        pytest.skip("Node unavailable")
    result=subprocess.run(
        ["node",str(ROOT/"tests/js/test_ml_buybox_dom.js")],
        cwd=ROOT,capture_output=True,text=True,timeout=15,
    )
    assert result.returncode==0,result.stderr
    assert "ML buy-box product validation cases OK" in result.stdout


def test_user_initiated_payment_diagnostics_redact_private_page_data():
    if shutil.which("node") is None:
        pytest.skip("Node unavailable")
    result=subprocess.run(
        ["node", str(ROOT/"tests/js/test_ml_payment_diagnostics.js")],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Read-only sanitized payment diagnostics OK" in result.stdout


def test_actual_chrome_product_error_codes_are_visible():
    code=EXT.read_text(encoding="utf-8")
    assert 'status:"PURCHASE_CONTROL_MISSING"' in code
    assert 'status:"PRODUCT_TITLE_MISSING"' in code
    assert "productFailures" in code
    assert "for(const item of cards.slice(0,10))" in code
    assert 'status:"VALIDATED"' in code
