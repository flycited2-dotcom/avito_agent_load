from decimal import Decimal
import pytest

from avito_bridge.ingest.manual_products import build_manual_raw_products


def test_builds_manual_raw_product_for_normal_pipeline():
    rows = build_manual_raw_products({
        "manual-rc-gr28hn-a1b2c3d4": {
            "brand": "ROYAL CLIMA",
            "title": "Инверторная сплит-система RCI-GR28HN",
            "series": "GRIDA DC EU Inverter",
            "category_id": 2,
            "btu": 9,
            "price": 26550,
            "stock": 1,
            "photos": ["https://splithome.ru/static/manual-photos/x.jpg"],
            "tech": {"Особенности": "Инвертор"},
        }
    })
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "manual"
    assert row.nc_code == "manual-rc-gr28hn-a1b2c3d4"
    assert row.price_override == Decimal("26550.0")
    assert row.forced is True
    assert row.image_urls == ["https://splithome.ru/static/manual-photos/x.jpg"]


@pytest.mark.parametrize("field,value", [("btu", 0), ("price", 0)])
def test_rejects_nonpositive_required_numbers(field, value):
    spec = {"brand": "B", "title": "T", "series": "S", "btu": 9, "price": 10}
    spec[field] = value
    with pytest.raises(ValueError, match=field):
        build_manual_raw_products({"manual-x": spec})


def test_rejects_unsafe_id_and_missing_identity_fields():
    valid = {"brand": "B", "title": "T", "series": "S", "btu": 9, "price": 10}
    with pytest.raises(ValueError, match="небезопасный"):
        build_manual_raw_products({"manual bad": valid})
    with pytest.raises(ValueError, match="обязательны"):
        build_manual_raw_products({"manual-x": {"btu": 9, "price": 10}})
