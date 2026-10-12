from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions/ml-chrome"


def test_manifest_scope_limited_to_ml_and_no_cookie_permissions():
    data=json.loads((EXT/"manifest.json").read_text(encoding="utf-8"))
    assert data["manifest_version"]==3
    assert "nativeMessaging" in data["permissions"]
    assert "alarms" in data["permissions"]
    assert "cookies" not in data["permissions"]
    assert "<all_urls>" not in str(data)
    assert "http://127.0.0.1" not in str(data)
    assert all("mercadolivre.com.br" in host for host in data["host_permissions"])
    assert data["background"]["service_worker"]=="background.js"
    assert data["action"]["default_popup"]=="popup.html"


def test_extension_starts_disabled_not_even_a_login_request():
    js=(EXT/"background.js").read_text(encoding="utf-8")
    assert "enabled:false" in js
    assert "periodInMinutes:5" in js
    assert "app.ml_chrome_bridge" not in js
    assert "sendNativeMessage" in js
    assert "chrome.cookies.get" not in js
    assert "chrome.identity.launchWebAuthFlow" not in js
    assert "document.cookie" not in js
    assert "chrome.cookies" not in js
    # Read-only page diagnostics may detect CAPTCHA text; no challenge bypass APIs.
    assert "chrome.webRequest" not in js
    assert "captcha.solve" not in js.lower()
    assert "remote-debugging-port" not in js
    assert "LINK_GERADO" in js
    assert "buy_now_enabled:true" in js


@pytest.mark.parametrize("filename", ["background.js", "popup.js"])
def test_javascript_parses_on_node_if_installed(filename):
    if shutil.which("node") is None:
        pytest.skip("Node runtime unavailable for JS grammar check")
    result = subprocess.run(
        ["node", "--check", str(EXT/filename)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_popup_has_explicit_activation_controls():
    html=(EXT/"popup.html").read_text(encoding="utf-8")
    js=(EXT/"popup.js").read_text(encoding="utf-8")
    assert 'id="enabled"' in html
    assert 'id="register"' in html
    assert 'id="once"' in html
    assert "runOnce" in js
    assert "setGenerator" in js


def test_no_browser_download_required_for_extension():
    docs=(ROOT/"docs/MERCADOLIVRE_CHROME_EXTENSAO.md").read_text(encoding="utf-8")
    assert "chrome://extensions" in docs
    assert "scripts.install_ml_chrome_bridge" in docs
    assert "playwright install" in docs
