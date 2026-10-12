from __future__ import annotations

import io
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import mercadolivre_oauth as oauth


class Response(io.BytesIO):
    pass


def _env(monkeypatch):
    monkeypatch.setenv("MERCADOLIVRE_APP_ID", "1234567")
    monkeypatch.setenv("MERCADOLIVRE_CLIENT_SECRET", "do-not-publish-me")
    monkeypatch.setenv("MERCADOLIVRE_REDIRECT_URI", "https://iagoluch.github.io/bot-afiliado/")
    monkeypatch.setenv("MERCADOLIVRE_PKCE_ENABLED", "false")


def _token_response(*, access="access-test", refresh="refresh-test"):
    return Response(json.dumps({
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": 21600,
        "user_id": 123,
        "scope": "offline_access read",
    }).encode())


def test_oauth_start_creates_private_state_and_url(tmp_path, monkeypatch):
    _env(monkeypatch)
    pending = tmp_path / "pending.json"
    url = oauth.start(pending_path=pending)
    p = urlsplit(url)
    q = parse_qs(p.query)
    assert p.scheme == "https" and p.netloc == "auth.mercadolivre.com.br"
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["1234567"]
    assert q["redirect_uri"] == ["https://iagoluch.github.io/bot-afiliado/"]
    assert len(q["state"][0]) > 32
    assert "do-not-publish-me" not in url
    assert "code_challenge" not in q
    assert json.loads(pending.read_text())["state"] == q["state"][0]
    if os.name == "posix":
        assert stat.S_IMODE(pending.stat().st_mode) == 0o600


def test_oauth_pkce_s256_when_enabled(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("MERCADOLIVRE_PKCE_ENABLED", "true")
    p = tmp_path / "pending.json"
    q = parse_qs(urlsplit(oauth.start(pending_path=p)).query)
    assert q["code_challenge_method"] == ["S256"]
    assert len(q["code_challenge"][0]) > 30
    assert json.loads(p.read_text())["verifier"]


def test_oauth_wrong_state_fails_before_network(tmp_path, monkeypatch):
    _env(monkeypatch)
    pending = tmp_path / "pending.json"
    oauth.start(pending_path=pending)
    with pytest.raises(ValueError, match="State"):
        oauth.finish("auth-code", "wrong", pending_path=pending, token_path=tmp_path / "tokens.json",
                     opener=lambda *_a, **_k: pytest.fail("network attempt"))
    assert pending.exists()


def test_oauth_expired_state_fails(tmp_path, monkeypatch):
    _env(monkeypatch)
    pending = tmp_path / "pending.json"
    oauth.start(pending_path=pending)
    record = json.loads(pending.read_text())
    record["created_at"] = time.time() - 9999
    pending.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="antiga"):
        oauth.finish("auth-code", record["state"], pending_path=pending, token_path=tmp_path / "tokens.json",
                     opener=lambda *_a, **_k: pytest.fail("network attempt"))


def test_finish_exchanges_code_in_body_and_hides_secrets(tmp_path, monkeypatch):
    _env(monkeypatch)
    pending = tmp_path / "pending.json"
    token_path = tmp_path / "tokens.json"
    oauth.start(pending_path=pending)
    state = json.loads(pending.read_text())["state"]
    calls = []

    def fake(req, timeout):
        calls.append((req.full_url, parse_qs(req.data.decode()), timeout))
        return _token_response()

    result = oauth.finish("TG-example-code", state, pending_path=pending, token_path=token_path, opener=fake)
    assert result == {"connected": True, "expires_in_seconds": 21600, "user_id": 123}
    assert calls[0][0] == "https://api.mercadolibre.com/oauth/token"
    assert calls[0][1]["redirect_uri"] == ["https://iagoluch.github.io/bot-afiliado/"]
    assert calls[0][1]["client_secret"] == ["do-not-publish-me"]
    assert not pending.exists()
    assert json.loads(token_path.read_text())["refresh_token"] == "refresh-test"
    assert "access-test" not in str(result)
    if os.name == "posix":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_invalid_redirect_and_missing_secret(monkeypatch, tmp_path):
    _env(monkeypatch)
    monkeypatch.setenv("MERCADOLIVRE_REDIRECT_URI", "http://localhost:8000/")
    with pytest.raises(ValueError, match="HTTPS"):
        oauth.start(pending_path=tmp_path / "pending.json")
    monkeypatch.setenv("MERCADOLIVRE_REDIRECT_URI", "https://iagoluch.github.io/bot-afiliado/")
    monkeypatch.setenv("MERCADOLIVRE_CLIENT_SECRET", "")
    oauth.start(pending_path=tmp_path / "pending.json")
    state = json.loads((tmp_path / "pending.json").read_text())["state"]
    with pytest.raises(ValueError, match="CLIENT_SECRET"):
        oauth.finish("code", state, pending_path=tmp_path / "pending.json", token_path=tmp_path / "tokens.json")


def test_refresh_stays_idle_with_valid_token(tmp_path, monkeypatch):
    _env(monkeypatch)
    token_path = tmp_path / "token.json"
    oauth._atomic_private_json(token_path, {
        "access_token": "valid", "refresh_token": "unused",
        "expires_at": time.time() + 3600, "user_id": 123,
    })
    result = oauth.refresh(token_path=token_path, opener=lambda *_a, **_k: pytest.fail("unnecessary call"))
    assert not result["refreshed"]


def test_refresh_rotates_single_use_secret(tmp_path, monkeypatch):
    _env(monkeypatch)
    token_path = tmp_path / "token.json"
    oauth._atomic_private_json(token_path, {
        "access_token": "old", "refresh_token": "once-only",
        "expires_at": time.time() - 10, "user_id": 123,
    })
    calls = []

    def fake(req, timeout):
        calls.append(parse_qs(req.data.decode()))
        return _token_response(access="new", refresh="rotated")

    result = oauth.refresh(token_path=token_path, opener=fake)
    assert result["refreshed"]
    assert calls[0]["refresh_token"] == ["once-only"]
    assert json.loads(token_path.read_text())["refresh_token"] == "rotated"
    assert oauth.status(token_path=token_path)["access_token_valid"]


def test_pages_oauth_landing_has_no_external_dependencies():
    root_page = Path(__file__).resolve().parent.parent / "index.html"
    html = root_page.read_text(encoding="utf-8")
    assert "history.replaceState" in html
    assert "navigator.clipboard.writeText" in html
    assert 'connect-src &#39;none&#39;' not in html  # ensure literal CSP, not HTML-escaped
    assert "connect-src 'none'" in html
    assert "<script src=" not in html
    assert "Client Secret" in html
