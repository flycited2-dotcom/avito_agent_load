import sqlite3
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from avito_bridge.cards_pipeline import (
    CardJobStore,
    FotogenConfig,
    _http_get,
    _safe_output_path,
    _validate_public_http_url,
    done_results,
    failed_inputs,
    run_once,
    specs_text,
    submit_card_job,
    wake_agent,
)
from avito_bridge.catalog.series import group_by_series
from avito_bridge.models import Offer


def test_wake_agent_sets_start_flag(tmp_path):
    db = str(tmp_path / "q.db")
    sqlite3.connect(db).close()
    wake_agent(db)
    con = sqlite3.connect(db)
    assert con.execute("SELECT value FROM flags WHERE key='agent_command'").fetchone()[0] == "start"
    con.close()


def _make_queue_db(tmp_path, rows):
    db = tmp_path / "queue.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT, input_filename TEXT, output_filename TEXT)")
    for st, inf, outf in rows:
        con.execute("INSERT INTO jobs(status,input_filename,output_filename) VALUES(?,?,?)", (st, inf, outf))
    con.commit(); con.close()
    return str(db)


def _cfg(tmp_path, **kw):
    base = dict(api_url="http://x", token="t", chat_id=1,
                queue_db=str(tmp_path / "q.db"), output_dir=str(tmp_path / "out"),
                cards_dir=str(tmp_path / "cards"), per_run=8)
    base.update(kw)
    return FotogenConfig(**base)


def _write_image(path, image_format="PNG", size=(8, 8)):
    Image.new("RGB", size, "white").save(path, format=image_format)


def test_submit_card_job_returns_queued_name():
    def handler(req):
        assert req.url.path == "/api/submit-job"
        return httpx.Response(200, json={"ok": True, "queued": "ext_123.jpg"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    cfg = FotogenConfig(api_url="http://x", token="t", chat_id=1, queue_db="", output_dir="", cards_dir="")
    assert submit_card_job(cfg, b"img", "Ballu", "Olympio", "specs", http=http) == "ext_123.jpg"


def test_done_and_failed_queries(tmp_path):
    db = _make_queue_db(tmp_path, [("done", "a.jpg", "card_a.png"),
                                   ("failed", "b.jpg", None),
                                   ("processing", "c.jpg", None)])
    assert done_results(db, ["a.jpg", "b.jpg", "c.jpg"]) == {"a.jpg": "card_a.png"}
    assert failed_inputs(db, ["a.jpg", "b.jpg", "c.jpg"]) == {"b.jpg"}


def test_card_job_store(tmp_path):
    s = CardJobStore(tmp_path / "s.db")
    s.record("k1", "ext_1.jpg", "pending")
    assert s.get("k1") == ("ext_1.jpg", "pending", 0)
    assert s.pending() == [("k1", "ext_1.jpg")]
    s.record("k1", "ext_1.jpg", "done")
    assert s.pending() == []


def test_specs_text_filters():
    t = specs_text({"Бренд": "X", "Холод, кВт": "3.5", "Пусто": ""})
    assert "Холод, кВт: 3.5" in t and "Бренд" not in t and "Пусто" not in t


def _o(sku, btu, photo, series="Olympio"):
    return Offer(supplier_sku=sku, source="breeze", brand="Ballu", model=f"{series} {btu}",
                 category_id=2, btu_calc=btu, attrs={"Холод, кВт": "2.0"}, cost=Decimal("1"),
                 retail_ref=None, stock=1, photos=[photo] if photo else [], series=series,
                 content_hash="h")


def test_card_input_photo_prefers_indoor_for_daichi():
    from avito_bridge.content.cards import card_input_photo
    daichi = _o("daichi:1", 7, "montage.jpg")
    daichi.source = "daichi"
    daichi.photos = ["montage.jpg", "indoor.jpg", "outdoor.jpg"]
    assert card_input_photo(daichi) == "indoor.jpg"        # daichi: пропускаем монтаж [0]
    breeze = _o("breeze:1", 7, "indoor.jpg")
    breeze.photos = ["indoor.jpg", "x.jpg"]
    assert card_input_photo(breeze) == "indoor.jpg"        # breeze: [0] уже норм


def test_run_once_submits_and_publishes(tmp_path):
    # одна серия без карточки → submit; и одна готовая в очереди → publish
    out = tmp_path / "out"; out.mkdir()
    _write_image(out / "card_ready.png")
    db = _make_queue_db(tmp_path, [("done", "ext_ready.jpg", "card_ready.png")])
    cfg = _cfg(tmp_path, queue_db=db, output_dir=str(out))
    store = CardJobStore(tmp_path / "s.db")
    from avito_bridge.content.cards import card_key
    store.record(card_key("breeze:NC1"), "ext_ready.jpg", "pending")   # ждёт готовую

    groups = group_by_series([_o("breeze:NC1", 7, "http://p/1.jpg", series="Olympio"),
                              _o("breeze:NC2", 9, "http://p/2.jpg", series="Gloria")])

    def handler(req):
        return httpx.Response(200, json={"queued": "ext_new.jpg"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    submitted, published = run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img")

    assert published == 1                                  # готовая скопирована
    published_card = tmp_path / "cards" / f"{card_key('breeze:NC1')}.png"
    assert published_card.is_file()
    with Image.open(published_card) as image:
        image.load()
        assert image.format == "PNG"
    assert submitted >= 1                                  # новая серия поставлена в очередь


@pytest.mark.parametrize(
    ("filename", "writer", "message"),
    [
        ("corrupt.jpg", lambda path: path.write_bytes(b"not-an-image"), "valid JPEG/PNG"),
        (
            "disguised.jpg",
            lambda path: _write_image(path, image_format="GIF"),
            "JPEG or PNG",
        ),
    ],
)
def test_run_once_rejects_invalid_agent_output(
    tmp_path, filename, writer, message
):
    out = tmp_path / "out"
    out.mkdir()
    source = out / filename
    writer(source)
    db = _make_queue_db(tmp_path, [("done", "queued.jpg", filename)])
    cfg = _cfg(tmp_path, queue_db=db, output_dir=str(out))
    store = CardJobStore(tmp_path / "state.db")
    from avito_bridge.content.cards import card_key

    key = card_key("breeze:NC-invalid")
    store.record(key, "queued.jpg", "pending")

    with pytest.raises(ValueError, match=message):
        run_once([], cfg, store)

    assert store.get(key)[1] == "pending"
    assert not list((tmp_path / "cards").glob(f"{key}.*"))


def test_run_once_uses_manual_card_brief_override(tmp_path):
    """Владелец может задать своё УТП для карточки (напр. у товара «под заказ» с бедными
    техданными) — вместо авто-сгенерированного card_brief() (бренд/тип/размер/инвертор)."""
    out = tmp_path / "out"; out.mkdir()
    cfg = _cfg(tmp_path, queue_db=_make_queue_db(tmp_path, []), output_dir=str(out))
    store = CardJobStore(tmp_path / "s.db")
    groups = group_by_series([_o("breeze:NC9", 9, "http://p/9.jpg", series="Gloria")])
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode("utf-8", errors="ignore")
        return httpx.Response(200, json={"queued": "ext.jpg"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img",
            manual_brief={"NC9": "Тихий, мощный, Wi-Fi"})
    assert "Тихий, мощный, Wi-Fi" in seen["body"]


def test_run_once_uses_per_series_mode(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    cfg = _cfg(tmp_path, queue_db=_make_queue_db(tmp_path, []), output_dir=str(out),
               mode="conditioner", modes={"breeze|ballu|gloria": "mcp"})
    store = CardJobStore(tmp_path / "s.db")
    groups = group_by_series([_o("breeze:NC2", 9, "http://p/2.jpg", series="Gloria")])
    seen = {}

    def handler(req):
        seen["mcp"] = b"mcp" in req.content                # mode=mcp ушёл в форму
        return httpx.Response(200, json={"queued": "ext.jpg"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img")
    assert seen.get("mcp") is True


def test_run_once_retries_failed_until_cap(tmp_path):
    from avito_bridge.content.cards import card_key
    out = tmp_path / "out"; out.mkdir()
    cfg = _cfg(tmp_path, queue_db=_make_queue_db(tmp_path, []), output_dir=str(out))
    store = CardJobStore(tmp_path / "s.db")
    k = card_key("breeze:NC2")
    store.record(k, "old.jpg", "failed", tries=1)          # 1 неудачная попытка
    groups = group_by_series([_o("breeze:NC2", 9, "http://p/2.jpg", series="Gloria")])

    def handler(req):
        return httpx.Response(200, json={"queued": "ext_new.jpg"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    submitted, _ = run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img")
    assert submitted == 1                                  # failed с запасом попыток → переотправлен
    assert store.get(k)[2] == 2                            # счётчик попыток вырос


def test_run_once_gives_up_after_max_tries(tmp_path):
    from avito_bridge.cards_pipeline import MAX_TRIES
    from avito_bridge.content.cards import card_key
    out = tmp_path / "out"; out.mkdir()
    cfg = _cfg(tmp_path, queue_db=_make_queue_db(tmp_path, []), output_dir=str(out))
    store = CardJobStore(tmp_path / "s.db")
    k = card_key("breeze:NC2")
    store.record(k, "old.jpg", "failed", tries=MAX_TRIES)  # попытки исчерпаны
    groups = group_by_series([_o("breeze:NC2", 9, "http://p/2.jpg", series="Gloria")])

    def handler(req):
        return httpx.Response(200, json={"queued": "x"})
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    submitted, _ = run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img")
    assert submitted == 0                                  # больше не долбим агента


def test_run_once_prints_reason_when_submit_fails(tmp_path, capsys):
    """Раньше ошибка отправки (сеть/API) проглатывалась молча (except Exception: continue) —
    submitted=0 без единого следа причины. Найдено вживую: GUI показывает вывод cards_run
    пользователю напрямую (кнопка «Сгенерировать карточку»), молчание невозможно продиагностировать."""
    out = tmp_path / "out"; out.mkdir()
    cfg = _cfg(tmp_path, queue_db=_make_queue_db(tmp_path, []), output_dir=str(out))
    store = CardJobStore(tmp_path / "s.db")
    groups = group_by_series([_o("breeze:NC3", 9, "http://p/3.jpg", series="Aura")])

    def handler(req):
        return httpx.Response(500, text="internal error")
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    submitted, _ = run_once(groups, cfg, store, http=http, fetch_photo=lambda u: b"img")

    assert submitted == 0
    assert "breeze|ballu|aura" in capsys.readouterr().out   # причина видна, не молчание


def test_safe_output_path_rejects_agent_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        _safe_output_path(tmp_path, "../outside.png")
    with pytest.raises(ValueError, match="Unsafe"):
        _safe_output_path(tmp_path, "/tmp/outside.png")


def test_card_download_url_rejects_local_and_private_hosts(monkeypatch):
    with pytest.raises(ValueError, match="Local"):
        _validate_public_http_url("http://localhost/image.jpg")
    monkeypatch.setattr(
        "avito_bridge.cards_pipeline.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="Non-public"):
        _validate_public_http_url("https://images.example.test/photo.jpg")


class _PeerNetworkStream:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return (self.address, 443) if name == "server_addr" else None


def _mock_card_download(monkeypatch, handler, peer="93.184.216.34"):
    """Use the real httpx client lifecycle while keeping every byte in memory."""
    real_client = httpx.Client
    created = []

    def client_factory(**kwargs):
        assert kwargs["headers"]["User-Agent"] == "AvitoBridge/1.0"
        assert kwargs["timeout"] == 30
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

        def connected_handler(request):
            response = handler(request)
            if peer is not None:
                response.extensions["network_stream"] = _PeerNetworkStream(peer)
            return response

        client = real_client(
            transport=httpx.MockTransport(connected_handler), **kwargs
        )
        created.append(client)
        return client

    monkeypatch.setattr(
        "avito_bridge.cards_pipeline.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr("avito_bridge.cards_pipeline.httpx.Client", client_factory)
    return created


def test_http_get_returns_image_and_closes_owned_client(monkeypatch):
    def handler(request):
        assert request.url == httpx.URL("https://cdn.example.test/card.jpg")
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg; charset=binary"},
            content=b"jpeg-bytes",
        )

    created = _mock_card_download(monkeypatch, handler)

    assert _http_get("https://cdn.example.test/card.jpg") == b"jpeg-bytes"
    assert len(created) == 1
    assert created[0].is_closed


def test_http_get_rejects_ssrf_target_before_opening_http_client(monkeypatch):
    def unexpected_client(**_kwargs):
        raise AssertionError("HTTP client must not be opened for a forbidden URL")

    monkeypatch.setattr("avito_bridge.cards_pipeline.httpx.Client", unexpected_client)

    with pytest.raises(ValueError, match="Local"):
        _http_get("http://localhost/private-card.jpg")


def test_http_get_rejects_private_peer_after_public_dns_resolution(monkeypatch):
    created = _mock_card_download(
        monkeypatch,
        lambda _request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"private"
        ),
        peer="127.0.0.1",
    )

    with pytest.raises(ValueError, match="Non-public connected"):
        _http_get("https://cdn.example.test/card.jpg")

    assert len(created) == 1
    assert created[0].is_closed


def test_http_get_fails_closed_when_transport_hides_peer(monkeypatch):
    _mock_card_download(
        monkeypatch,
        lambda _request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"unknown"
        ),
        peer=None,
    )

    with pytest.raises(ValueError, match="Cannot verify"):
        _http_get("https://cdn.example.test/card.jpg")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
            "non-image",
        ),
        (
            httpx.Response(
                200,
                headers={
                    "content-type": "image/jpeg",
                    "content-length": str(15 * 1024 * 1024 + 1),
                },
                content=b"x",
            ),
            "15 MiB",
        ),
        (
            httpx.Response(404, headers={"content-type": "image/jpeg"}, content=b"missing"),
            "404",
        ),
    ],
)
def test_http_get_rejects_unsafe_response_and_still_closes_client(
    monkeypatch, response, message
):
    created = _mock_card_download(monkeypatch, lambda _request: response)

    with pytest.raises((ValueError, httpx.HTTPStatusError), match=message):
        _http_get("https://cdn.example.test/card.jpg")

    assert len(created) == 1
    assert created[0].is_closed


def test_http_get_rejects_large_body_without_declared_length(monkeypatch):
    body = b"x" * (15 * 1024 * 1024 + 1)

    def handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            stream=httpx.ByteStream(body),
        )

    created = _mock_card_download(monkeypatch, handler)

    with pytest.raises(ValueError, match="15 MiB"):
        _http_get("https://cdn.example.test/card.png")

    assert created[0].is_closed
