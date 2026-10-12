from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from app.mercadolivre_catalog import CatalogProbeError, search_catalog


def _auth(tmp_path: Path) -> Path:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": "test-token-do-not-log",
        "expires_at": time.time() + 3600,
    }), encoding="utf-8")
    return path


def _response():
    return io.BytesIO(json.dumps({
        "paging": {"total": 1},
        "results": [{
            "id": "MLB9876543",
            "name": "SSD NVMe 1TB",
            "domain_id": "MLB-SOLID_STATE_DRIVES",
            "pictures": [
                {"url": "https://evil.example/secret.png"},
                {"url": "https://http2.mlstatic.com/produto-oficial.jpg"},
            ],
            "price": 200,
            "affiliate_url": "https://example.com/unsafe-commission",
        }],
    }).encode("utf-8"))


def test_search_only_official_endpoint_get_and_no_secrets(tmp_path):
    requests = []
    def opener(req, timeout):
        requests.append((req, timeout))
        return _response()
    result = search_catalog("SSD NVMe", limit=3, token_path=_auth(tmp_path), opener=opener)
    req, timeout = requests[0]
    assert len(requests) == 1 and timeout == 15
    assert req.get_method() == "GET"
    assert urllib.parse.urlsplit(req.full_url).path == "/products/search"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query) == {
        "site_id": ["MLB"], "status": ["active"], "q": ["SSD NVMe"], "limit": ["3"]
    }
    assert req.get_header("Authorization") == "Bearer test-token-do-not-log"
    assert result["status"] == "CATALOG_ACCESS_OK"
    assert result["results"][0]["image_url"] == "https://http2.mlstatic.com/produto-oficial.jpg"
    assert result["has_affiliate_link"] is False
    assert result["may_publish_to_telegram"] is False
    assert "price" not in result["results"][0]
    assert "affiliate_url" not in result["results"][0]
    assert "test-token" not in str(result)


def test_catalog_denied_reports_safe_error_without_http_body(tmp_path):
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b'{"token":"secret"}'))
    with pytest.raises(CatalogProbeError, match="403.*Acesso negado") as exc:
        search_catalog("mouse", token_path=_auth(tmp_path), opener=opener)
    assert "secret" not in str(exc.value)


def test_catalog_returns_429_without_retry(tmp_path):
    calls = []
    def opener(req, timeout):
        calls.append(True)
        raise urllib.error.HTTPError(req.full_url, 429, "too many", {}, None)
    with pytest.raises(CatalogProbeError, match="429"):
        search_catalog("mouse", token_path=_auth(tmp_path), opener=opener)
    assert len(calls) == 1


@pytest.mark.parametrize("query,limit", [
    ("", 5), ("x", 5), ("a" * 121, 5),
    ("ssd", 0), ("ssd", 11), ("evil\ninput", 5),
])
def test_invalid_search_never_uses_network(tmp_path, query, limit):
    with pytest.raises(ValueError):
        search_catalog(query, limit=limit, token_path=_auth(tmp_path),
                       opener=lambda *_a, **_k: pytest.fail("network not allowed"))


def test_missing_token_fails_closed(tmp_path):
    with pytest.raises(CatalogProbeError, match="OAuth nao configurado"):
        search_catalog("ssd", token_path=tmp_path / "absent.json",
                       opener=lambda *_a, **_k: pytest.fail("network not allowed"))


def test_expired_test_token_is_not_used(tmp_path):
    path = _auth(tmp_path)
    path.write_text(json.dumps({
        "access_token": "expired",
        "expires_at": time.time() - 40
    }))
    with pytest.raises(CatalogProbeError, match="expirado"):
        search_catalog("ssd", token_path=path,
                       opener=lambda *_a, **_k: pytest.fail("network not allowed"))


def test_malformed_response_fails_closed(tmp_path):
    with pytest.raises(CatalogProbeError, match="estrutura"):
        search_catalog("ssd", token_path=_auth(tmp_path),
                       opener=lambda *_a, **_k: io.BytesIO(b'{"results":{}}'))


def test_nonofficial_image_rejected(tmp_path):
    def opener(req, timeout):
        return io.BytesIO(json.dumps({
            "results":[{"id":"MLB123", "name":"Mouse", "pictures":[
                {"url":"https://http2.mlstatic.com.evil.test/x.png"},
                {"url":"http://http2.mlstatic.com/x.png"}]}]
        }).encode())
    result=search_catalog("mouse",token_path=_auth(tmp_path),opener=opener)
    assert result["results"][0]["image_url"] is None
