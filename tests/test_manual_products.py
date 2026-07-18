from decimal import Decimal
from types import SimpleNamespace
import pytest

from avito_bridge.ingest.manual_products import build_manual_offers, build_manual_raw_products


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


def _profile(name, source_options=None):
    return SimpleNamespace(profile_name=name, source_options=source_options or {})


def test_builds_legacy_conditioner_offer():
    offer = build_manual_offers({
        "manual-ac": {
            "brand": "Ballu",
            "title": "BSAG-09",
            "series": "Eco Inverter",
            "category_id": 2,
            "btu": 9,
            "price": 30000,
            "stock": 1,
            "photos": ["https://i/ac.jpg"],
            "tech": {"Тип компрессора": "Инвертор"},
        }
    }, _profile("conditioners"))[0]

    assert offer.supplier_sku == "manual:manual-ac"
    assert offer.category_id == 2
    assert offer.btu_calc == 9
    assert offer.price_override == Decimal("30000.0")
    assert offer.attrs["Тип компрессора"] == "Инвертор"


def test_builds_generic_carver_offer_with_description_and_avito_tags():
    offer = build_manual_offers({
        "manual-generator": {
            "brand": "CARVER",
            "title": "Генератор CARVER PPG-1900i",
            "group": "generator",
            "price": 43200,
            "stock": 1,
            "photos": ["https://i/g.jpg"],
            "description": "Компактный инверторный генератор.",
            "tech": {"Топливо": "Бензин"},
            "avito_tags": {"FuelType": "Бензин", "RatedPower": "1.7"},
        }
    }, _profile("carver"))[0]

    assert offer.category_id is None
    assert offer.btu_calc is None
    assert offer.series == "generator"
    assert offer.attrs["avito_tag:FuelType"] == "Бензин"
    assert offer.attrs["avito_tag:RatedPower"] == "1.7"
    assert "Компактный инверторный генератор." in offer.attrs["desc_long"]
    assert "Топливо: Бензин" in offer.attrs["desc_long"]


def test_appliance_group_tags_are_added_and_explicit_tags_take_precedence():
    cfg = _profile("appliances", {
        "group_tags": {
            "Миксеры": {"GoodsType": "Для кухни", "GoodsSubType": "Старая группа"},
        }
    })
    offer = build_manual_offers({
        "manual-mixer": {
            "brand": "Kitfort",
            "title": "Миксер KT-100",
            "group": "Миксеры",
            "price": 5990,
            "stock": 2,
            "photos": ["https://i/mixer.jpg"],
            "tech": {"Мощность": "600 Вт", "Количество скоростей": "5"},
            "avito_tags": {"GoodsSubType": "Мелкая кухонная техника"},
        }
    }, cfg)[0]

    assert offer.attrs["avito_tag:GoodsType"] == "Для кухни"
    assert offer.attrs["avito_tag:GoodsSubType"] == "Мелкая кухонная техника"
    assert "Мощность: 600 Вт" in offer.attrs["desc_long"]


def test_wreath_offer_does_not_require_series_or_conditioner_fields():
    offer = build_manual_offers({
        "manual-wreath": {
            "brand": "",
            "title": "Венок Аврора",
            "group": "wreath",
            "price": 3500,
            "stock": 1,
            "photos": ["https://i/wreath.jpg"],
            "tech": {"Форма": "Овальная", "Высота": "120 см"},
        }
    }, _profile("wreaths"))[0]

    assert offer.brand == ""
    assert offer.series == "wreath"
    assert offer.category_id is None
    assert "Форма: Овальная" in offer.attrs["desc_long"]


@pytest.mark.parametrize("field,value", [
    ("price", 0),
    ("stock", 0),
    ("photos", []),
])
def test_generic_manual_offer_rejects_missing_publication_data(field, value):
    spec = {
        "brand": "CARVER", "title": "Generator", "group": "generator",
        "price": 100, "stock": 1, "photos": ["https://i/g.jpg"],
    }
    spec[field] = value
    with pytest.raises(ValueError, match=field):
        build_manual_offers({"manual-generator": spec}, _profile("carver"))


def test_generic_manual_offer_rejects_unsafe_avito_tag():
    spec = {
        "brand": "CARVER", "title": "Generator", "group": "generator",
        "price": 100, "stock": 1, "photos": ["https://i/g.jpg"],
        "avito_tags": {"bad tag": "x"},
    }
    with pytest.raises(ValueError, match="avito_tags"):
        build_manual_offers({"manual-generator": spec}, _profile("carver"))
