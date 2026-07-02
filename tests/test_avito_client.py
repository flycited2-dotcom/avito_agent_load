import json
import httpx
from pathlib import Path
from avito_bridge.avito.client import AvitoClient, parse_report


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
    assert uploads[0]["upload_id"] == 559743947
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
    assert items[0]["ad_id"] == "0538c8c6f2190ea229023c7d"
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
