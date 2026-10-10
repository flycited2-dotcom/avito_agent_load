import pytest
from decimal import Decimal
from types import SimpleNamespace

from avito_bridge.ingest import sources
from avito_bridge.ingest.sources import fetch_profile_offers, get_source
from avito_bridge.models import Offer


def test_oasis_db_source_registered():
    fetch = get_source("oasis_db")
    assert callable(fetch)


def test_carver_xlsx_source_registered():
    assert callable(get_source("carver_xlsx"))


def test_manual_only_source_registered_and_empty():
    assert get_source("manual_only")(object()) == []


def test_unknown_source_raises_with_available_list():
    with pytest.raises(ValueError, match="oasis_db"):
        get_source("no_such_source")


def test_oasis_source_uses_configured_crimea_warehouse(monkeypatch):
    import decouple
    import avito_bridge.ingest as ingest
    from avito_bridge.ingest import oasis_db

    captured = {}

    def fake_config(name, default=None, cast=None):
        values = {
            "DB_NAME": "db",
            "DB_USER": "user",
            "DB_PASSWORD": "pass",
        }
        value = values.get(name, default)
        return cast(value) if cast else value

    def fake_fetch_raw_products(dsn, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(decouple, "config", fake_config)
    monkeypatch.setattr(oasis_db, "fetch_raw_products", fake_fetch_raw_products)
    monkeypatch.setattr(ingest, "collect_offers", lambda *args, **kwargs: [])
    cfg = SimpleNamespace(catalog=SimpleNamespace(
        crimea_warehouse="Севастополь",
        report_category_ids=[2],
        exclude_title_patterns=[],
        force_include={},
        manual_photos={},
        manual_price_override={},
    ))

    assert sources.fetch_oasis(cfg) == []
    assert captured["crimea"] == "Севастополь"


def test_radiator_taxonomy_uses_real_structured_attributes():
    offer = Offer(
        supplier_sku="rusklimat:r1",
        source="rusklimat",
        brand="Royal Thermo",
        model="Biliner 500 — 8 секций",
        category_id=118,
        cost=Decimal("10000"),
        stock=2,
        photos=["https://example.test/r.jpg"],
        series="Biliner",
        attrs={
            "Тип радиатора": "Секционный",
            "Материал": "Биметалл",
            "Количество секций": "8",
        },
    )
    cfg = SimpleNamespace(catalog=SimpleNamespace(
        category_tags={118: {
            "Category": "Ремонт и строительство",
            "GoodsType": "Камины и обогреватели",
        }},
        heat_pump_tags={},
        heat_pump_threshold=-20,
        supplier_photo_category_ids={118},
        category_labels={118: "Радиатор отопления"},
    ))

    assert sources._apply_oasis_taxonomy(offer, cfg) is True
    assert offer.attrs["avito_tag:Brand"] == "Royal Thermo"
    assert offer.attrs["avito_tag:Material"] == "Биметалл"
    assert offer.attrs["avito_tag:SectionQuantity"] == "8"


def test_panel_radiator_taxonomy_maps_source_value_and_defaults_one_section():
    offer = Offer(
        supplier_sku="rusklimat:p1", source="rusklimat",
        brand="Royal Thermo", model="Compact", category_id=118,
        cost=Decimal("10000"), stock=1,
        photos=["https://example.test/p.jpg"], series="Compact",
        attrs={"Тип радиатора": "Стальной панельный", "Материал": "Сталь"},
    )
    cfg = SimpleNamespace(catalog=SimpleNamespace(
        category_tags={118: {}}, heat_pump_tags={}, heat_pump_threshold=-20,
        supplier_photo_category_ids={118},
        category_labels={118: "Радиатор отопления"},
    ))

    assert sources._apply_oasis_taxonomy(offer, cfg) is True
    assert offer.attrs["avito_tag:RadiatorType"] == "Панельный"
    assert offer.attrs["avito_tag:SectionQuantity"] == "1"
    assert offer.attrs["meta:skip_product_type"] == "1"


def test_storage_water_heater_uses_home_appliance_taxonomy():
    offer = Offer(
        supplier_sku="rusklimat:w1", source="rusklimat",
        brand="Ballu", model="Shell 80", category_id=30,
        cost=Decimal("10000"), stock=1,
        photos=["https://example.test/w.jpg"], series="Shell", attrs={},
    )
    cfg = SimpleNamespace(catalog=SimpleNamespace(
        category_tags={30: {"GoodsType": "Для дома", "GoodsSubType": "Водонагреватели"}},
        heat_pump_tags={}, heat_pump_threshold=-20,
        supplier_photo_category_ids=set(),
        category_labels={30: "Накопительный водонагреватель"},
    ))

    assert sources._apply_oasis_taxonomy(offer, cfg) is True
    assert offer.attrs["avito_tag:GoodsType"] == "Для дома"
    assert offer.attrs["avito_tag:GoodsSubType"] == "Водонагреватели"
    assert offer.attrs["meta:skip_product_type"] == "1"


def test_heat_pump_gets_heater_taxonomy_and_no_ac_fields():
    offer = Offer(
        supplier_sku="breeze:hp",
        source="breeze",
        brand="Hisense",
        model="Vision Pro",
        category_id=2,
        cost=Decimal("10000"),
        stock=1,
        photos=["https://example.test/hp.jpg"],
        series="Vision Pro",
        attrs={"meta:heating_min_temp": "-25"},
    )
    cfg = SimpleNamespace(catalog=SimpleNamespace(
        category_tags={2: {"ProductType": "Кондиционеры и запчасти"}},
        heat_pump_tags={
            "ProductType": "Обогреватели",
            "GoodsSubType": "Тепловые насосы",
        },
        heat_pump_threshold=-20,
        supplier_photo_category_ids=set(),
        category_labels={2: "Инверторная сплит-система"},
    ))

    assert sources._apply_oasis_taxonomy(offer, cfg) is True
    assert offer.attrs["avito_tag:ProductType"] == "Обогреватели"
    assert offer.attrs["avito_tag:GoodsSubType"] == "Тепловые насосы"
    assert offer.attrs["meta:skip_ac_tags"] == "1"


def test_feed_path_default_and_profile_override(tmp_path):
    from avito_bridge.config import load_config
    base = ("cities:\n"
            "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
            "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
            "feed: {max_active_ads: 200}\n"
            "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
            "catalog: {report_category_ids: [], exclude_title_patterns: []}\n")
    (tmp_path / "a.yaml").write_text(base, encoding="utf-8")
    assert load_config(tmp_path / "a.yaml").feed_path == "feed_out/feed.xml"
    (tmp_path / "b.yaml").write_text(
        "profile: {name: wreaths, feed_path: feed_out/wreaths.xml}\n" + base,
        encoding="utf-8")
    assert load_config(tmp_path / "b.yaml").feed_path == "feed_out/wreaths.xml"


@pytest.mark.parametrize("profile_name,spec", [
    ("conditioners", {
        "brand": "Ballu", "title": "BSAG-09", "series": "Eco",
        "category_id": 2, "btu": 9, "price": 30000, "stock": 1,
        "photos": ["https://i/ac.jpg"],
    }),
    ("wreaths", {
        "brand": "", "title": "Венок Аврора", "group": "wreath",
        "price": 3500, "stock": 1, "photos": ["https://i/w.jpg"],
    }),
    ("appliances", {
        "brand": "Kitfort", "title": "Миксер KT-100", "group": "Миксеры",
        "price": 5990, "stock": 1, "photos": ["https://i/m.jpg"],
    }),
    ("carver", {
        "brand": "CARVER", "title": "PPG-1900i", "group": "generator",
        "price": 43200, "stock": 1, "photos": ["https://i/g.jpg"],
    }),
])
def test_profile_source_appends_manual_offer_exactly_once(monkeypatch, profile_name, spec):
    supplier = Offer(
        supplier_sku="fake:supplier", source="fake", brand="B", model="M",
        cost=Decimal("100"), stock=1, photos=["https://i/s.jpg"],
    )
    monkeypatch.setitem(sources.SOURCES, "fake", lambda cfg: [supplier])
    cfg = SimpleNamespace(
        source="fake",
        profile_name=profile_name,
        source_options={},
        catalog=SimpleNamespace(manual_products={"manual-x": spec}),
    )

    offers = fetch_profile_offers(cfg)

    assert [offer.supplier_sku for offer in offers] == ["fake:supplier", "manual:manual-x"]


@pytest.mark.parametrize(
    "wrapper,module_path,function_name",
    [
        (sources.fetch_ritualb2b, "avito_bridge.ingest.ritualb2b_site", "fetch_ritualb2b"),
        (sources.fetch_price_xls, "avito_bridge.ingest.price_xls", "fetch_price_xls"),
        (sources.fetch_carver_xlsx, "avito_bridge.ingest.carver_xlsx", "fetch_carver_xlsx"),
    ],
)
def test_source_wrappers_delegate_to_registered_adapter(
    monkeypatch, wrapper, module_path, function_name
):
    import importlib

    module = importlib.import_module(module_path)
    cfg = object()
    marker = [object()]
    monkeypatch.setattr(module, function_name, lambda loaded: marker)

    assert wrapper(cfg) is marker
