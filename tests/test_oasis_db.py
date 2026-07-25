from decimal import Decimal
import sys
import types
from avito_bridge.models import RawProduct
from avito_bridge.ingest.oasis_db import (row_to_raw, build_query_params, CRIMEA_QUERY,
                                          COOL_KW_QUERY, FORCE_QUERY, TECH_QUERY,
                                          apply_manual_price_override, fetch_raw_products,
                                          group_tech_rows)


def test_group_tech_rows():
    rows = [
        {"nc_code": "N1", "title": "Холод, кВт", "value": "3.5"},
        {"nc_code": "N1", "title": "Уровень шума", "value": "24 дБ"},
        {"nc_code": "N1", "title": "Пусто", "value": ""},          # пустое значение — пропуск
        {"nc_code": "N1", "title": "Холод, кВт", "value": "9.9"},  # дубль title — не перезаписывает
        {"nc_code": "N2", "title": "Тип", "value": "инвертор"},
    ]
    out = group_tech_rows(rows)
    assert out["N1"] == {"Холод, кВт": "3.5", "Уровень шума": "24 дБ"}
    assert out["N2"] == {"Тип": "инвертор"}


def test_row_to_raw_maps_columns():
    row = {"source": "rusklimat", "nc_code": "NC7", "brand": "Ballu",
           "title": "Ballu Olympio 07", "series": None, "category_id": 2,
           "btu_calc": 7, "price_wholesale": Decimal("30000"),
           "price_base": Decimal("21000"), "crimea_qty": 4,
           "image_urls": ["https://i/1.jpg", "https://i/2.jpg"]}      # все фото товара
    r = row_to_raw(row)
    assert r.source == "rusklimat" and r.nc_code == "NC7"
    assert r.price_base == Decimal("21000") and r.stock_qty == 4
    assert r.image_urls == ["https://i/1.jpg", "https://i/2.jpg"]


def test_row_to_raw_handles_null_image():
    r = row_to_raw({"source": "daichi", "nc_code": "N", "brand": "B", "title": "T",
                    "series": "S", "category_id": 6, "btu_calc": 60,
                    "price_wholesale": Decimal("1"), "price_base": None,
                    "crimea_qty": 0, "image_urls": None})
    assert r.image_urls == []


def test_query_params_use_config():
    p = build_query_params(crimea="Симферополь", cats=[2, 6, 7], deny=["%мульти%"])
    assert p["crimea"] == "Симферополь" and p["cats"] == [2, 6, 7]


def test_query_text_targets_crimea_warehouse():
    assert "warehouse = %(crimea)s" in CRIMEA_QUERY
    assert "btu_calc > 0" in CRIMEA_QUERY


def test_apply_manual_price_override_sets_price_on_regular_in_stock_raw():
    """Ручная цена должна работать и для ОБЫЧНОГО товара в наличии (не только для forced) —
    владелец хочет иметь возможность подправить цену у любой серии из GUI."""
    raw = RawProduct(source="breeze", nc_code="NC1", title="X", price_wholesale=Decimal("10000"))
    apply_manual_price_override([raw], {"NC1": 24990})
    assert raw.price_override == Decimal("24990")


def test_apply_manual_price_override_ignores_unmatched_nc():
    raw = RawProduct(source="breeze", nc_code="NC1", title="X")
    apply_manual_price_override([raw], {"NC2": 24990})
    assert raw.price_override is None


def test_apply_manual_price_override_handles_empty_overrides():
    raw = RawProduct(source="breeze", nc_code="NC1", title="X")
    apply_manual_price_override([raw], {})
    assert raw.price_override is None


def test_fetch_raw_products_uses_read_only_timeouts_and_applies_overrides(
    monkeypatch,
):
    base_row = {
        "source": "breeze",
        "nc_code": "NC1",
        "brand": "Ballu",
        "title": "Ballu Eco 7",
        "series": "Eco",
        "category_id": 2,
        "btu_calc": 7,
        "price_wholesale": Decimal("10000"),
        "price_base": None,
        "crimea_qty": 2,
        "image_urls": ["https://supplier/old.jpg"],
    }
    forced_row = {
        **base_row,
        "nc_code": "NC2",
        "title": "Ballu Eco 9",
        "btu_calc": 9,
        "crimea_qty": 0,
        "image_urls": [],
    }
    responses = {
        CRIMEA_QUERY: [base_row],
        FORCE_QUERY: [forced_row],
        TECH_QUERY: [
            {"nc_code": "NC1", "title": "Шум", "value": "24"},
            {"nc_code": "NC2", "title": "Шум", "value": "26"},
        ],
        COOL_KW_QUERY: [
            {
                "nc_code": "NC1",
                "title": "Холодопроизводительность (кВт)",
                "value": "2.1",
            }
        ],
    }

    class FakeCursor:
        query = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params):
            self.query = query

        def fetchall(self):
            return responses[self.query]

    class FakeConnection:
        closed = False

        def cursor(self, **kwargs):
            return FakeCursor()

        def close(self):
            self.closed = True

    connection = FakeConnection()
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return connection

    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.connect = connect
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object()
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)

    raws = fetch_raw_products(
        {
            "host": "db",
            "port": 5432,
            "dbname": "oasis",
            "user": "reader",
            "password": "secret",
        },
        "Симферополь",
        [2],
        [],
        force_include={"NC2": {"price": 19990, "series": "Eco order"}},
        manual_photos={"NC1": "https://owner/photo.jpg"},
        manual_price_override={"NC1": 15990},
        connect_timeout=7,
        statement_timeout_ms=12_345,
    )

    assert captured["connect_timeout"] == 7
    assert "statement_timeout=12345" in captured["options"]
    assert "default_transaction_read_only=on" in captured["options"]
    assert connection.closed is True
    by_nc = {raw.nc_code: raw for raw in raws}
    assert by_nc["NC1"].image_urls == ["https://owner/photo.jpg"]
    assert by_nc["NC1"].price_override == Decimal("15990")
    assert by_nc["NC1"].tech == {"Шум": "24"}
    assert by_nc["NC1"].cool_kw == 2.1
    assert by_nc["NC2"].forced is True
    assert by_nc["NC2"].stock_qty == 1
    assert by_nc["NC2"].series == "Eco order"
    assert by_nc["NC2"].price_override == Decimal("19990")
