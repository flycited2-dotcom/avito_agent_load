from decimal import Decimal
from avito_bridge.models import RawProduct
from avito_bridge.ingest.oasis_db import (row_to_raw, build_query_params, CRIMEA_QUERY,
                                          group_tech_rows, apply_manual_price_override)


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
