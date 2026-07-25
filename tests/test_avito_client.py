import json
from pathlib import Path

import httpx
import pytest

from avito_bridge.avito.client import AvitoClient, ep_last_report, parse_report


def _client(handler):
    transport = httpx.MockTransport(handler)
    return AvitoClient(client_id="id", client_secret="secret",
                       http=httpx.Client(transport=transport, base_url="https://api.avito.ru"))


def test_get_token_caches():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/token":
            calls["n"] += 1
            return httpx.Response(200, json={"access_token": "T123", "expires_in": 86400})
        return httpx.Response(404)

    c = _client(handler)
    assert c.get_token() == "T123"
    assert c.get_token() == "T123"
    assert calls["n"] == 1                 # второй раз — из кэша


def test_get_self_returns_user_id():
    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        if req.url.path == "/core/v1/accounts/self":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(404)

    assert _client(handler).get_self_id() == 42


def test_parse_last_report_extracts_rejections():
    rep = json.loads((Path(__file__).parent / "fixtures" / "avito_last_report.json").read_text("utf-8"))
    published, rejected = parse_report(rep)
    assert "abc" in published
    assert rejected["def"] == ["Запрещённое слово в описании"]


def test_list_uploads_returns_uploads_list():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "avito_v4_uploads.json").read_text("utf-8"))

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        if req.url.path == "/autoload/v4/uploads":
            return httpx.Response(200, json=fixture)
        return httpx.Response(404)

    uploads = _client(handler).list_uploads()
    assert len(uploads) == 2
    assert uploads[0]["upload_id"] == 1001
    assert uploads[0]["stats"]["count"] == 57


def test_last_successful_items_returns_items_list():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "avito_v4_items.json").read_text("utf-8"))

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        if req.url.path == "/autoload/v4/uploads/last_successful/items":
            return httpx.Response(200, json=fixture)
        return httpx.Response(404)

    items = _client(handler).last_successful_items()
    assert len(items) == 2
    assert items[0]["ad_id"] == "synthetic-ad-id-1"
    assert items[0]["avito_status"] == "active"


def test_last_successful_items_walks_all_pages():
    """Реальный ответ (живой запрос 2026-07-02): perPage фиксирован сервером = 20,
    meta = {perPage, page, pages, total}. Без пагинации статус получали только первые 20
    объявлений из 65 — у остальных серий в студии врали прочерком."""
    def page_items(page, n):
        return [{"ad_id": f"p{page}-{i}", "avito_status": "active"} for i in range(n)]

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        if req.url.path == "/autoload/v4/uploads/last_successful/items":
            page = int(req.url.params.get("page", "1"))
            n = 20 if page < 3 else 5          # 20+20+5 = 45, страниц 3
            return httpx.Response(200, json={
                "items": page_items(page, n),
                "meta": {"perPage": 20, "page": page, "pages": 3, "total": 45}})
        return httpx.Response(404)

    items = _client(handler).last_successful_items()
    assert len(items) == 45
    assert items[0]["ad_id"] == "p1-0"
    assert items[-1]["ad_id"] == "p3-4"


def test_status_by_ad_id_indexes_items():
    from avito_bridge.avito.client import status_by_ad_id
    items = [{"ad_id": "a1", "avito_status": "active"}, {"ad_id": "a2", "avito_status": "blocked"}]
    idx = status_by_ad_id(items)
    assert idx["a1"]["avito_status"] == "active"
    assert idx["a2"]["avito_status"] == "blocked"
    assert "missing" not in idx


def test_retries_transient_api_failure(monkeypatch):
    calls = {"uploads": 0}

    def handler(req):
        if req.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        if req.url.path == "/autoload/v4/uploads":
            calls["uploads"] += 1
            if calls["uploads"] == 1:
                return httpx.Response(503, text="temporary")
            return httpx.Response(200, json={"uploads": [{"upload_id": 1}]})
        return httpx.Response(404)

    monkeypatch.setattr("avito_bridge.avito.client.time.sleep", lambda _delay: None)
    client = _client(handler)
    assert client.list_uploads() == [{"upload_id": 1}]
    assert calls["uploads"] == 2


def test_last_report_endpoint_and_response():
    seen = {}

    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "REPORT-TOKEN", "expires_in": 999})
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"items": [{"ad_id": "ad-1", "status": "published"}]})

    client = _client(handler)
    try:
        assert ep_last_report(123456789) == (
            "/autoload/v1/accounts/123456789/reports/last_report/"
        )
        assert client.get_last_report(123456789) == {
            "items": [{"ad_id": "ad-1", "status": "published"}]
        }
        assert seen == {
            "path": "/autoload/v1/accounts/123456789/reports/last_report/",
            "authorization": "Bearer REPORT-TOKEN",
        }
    finally:
        client.http.close()


def test_get_last_report_propagates_http_error():
    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 999})
        return httpx.Response(403, json={"error": "forbidden"})

    client = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError, match="403"):
            client.get_last_report(42)
    finally:
        client.http.close()


def test_close_only_closes_owned_http_client():
    owned = AvitoClient("id", "secret")
    owned_http = owned.http
    owned.close()
    assert owned_http.is_closed

    injected_http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200))
    )
    borrowed = AvitoClient("id", "secret", http=injected_http)
    borrowed.close()
    assert not injected_http.is_closed
    injected_http.close()


def test_context_manager_returns_client_and_closes_owned_http_even_on_error():
    client = AvitoClient("id", "secret")
    owned_http = client.http

    with pytest.raises(RuntimeError, match="inside context"):
        with client as entered:
            assert entered is client
            raise RuntimeError("inside context")

    assert owned_http.is_closed


def test_context_manager_does_not_close_borrowed_http():
    injected_http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200))
    )

    with AvitoClient("id", "secret", http=injected_http) as entered:
        assert entered.http is injected_http

    assert not injected_http.is_closed
    injected_http.close()
