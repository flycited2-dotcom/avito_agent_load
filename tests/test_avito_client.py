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
