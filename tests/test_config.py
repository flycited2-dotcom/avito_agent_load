from pathlib import Path

import pytest

from avito_bridge.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_load_config_parses_cities_and_pricing(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog: {report_category_ids: [2,6,7], exclude_title_patterns: ['%мульти%'], "
        "crimea_warehouse: Севастополь, site_base_url: 'https://catalog.example'}\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.cities[0].avito_location == "Симферополь"
    assert cfg.pricing.default_markup_pct == 5
    assert cfg.feed.max_active_ads == 200
    assert cfg.catalog.report_category_ids == [2, 6, 7]
    assert cfg.catalog.crimea_warehouse == "Севастополь"
    assert cfg.catalog.site_base_url == "https://catalog.example"


def test_load_config_parses_manual_price_override(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog:\n"
        "  report_category_ids: [2,6,7]\n"
        "  exclude_title_patterns: []\n"
        "  manual_price_override:\n"
        "    \"НС-1\": 24990\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.catalog.manual_price_override == {"НС-1": 24990}


def test_load_config_parses_excluded_sources_case_insensitively(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "catalog:\n"
        "  report_category_ids: [2]\n"
        "  exclude_title_patterns: []\n"
        "  excluded_sources: [RusKlimat, ' daichi ']\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")

    assert cfg.catalog.excluded_sources == {"rusklimat", "daichi"}


def test_load_config_parses_feed_override_policy_and_tag_order(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "feed:\n"
        "  base_tags: {Category: Бытовая техника}\n"
        "  overridable_tags: [Category, ProductType]\n"
        "  tag_order: [Category, GoodsType, GoodsSubType, ProductType, Vendor]\n",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")

    assert cfg.feed.overridable_tags == {"Category", "ProductType"}
    assert cfg.feed.tag_order == [
        "Category",
        "GoodsType",
        "GoodsSubType",
        "ProductType",
        "Vendor",
    ]


def test_load_config_rejects_duplicate_feed_override_tags(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "feed:\n"
        "  overridable_tags: [Vendor, Vendor]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="повторяющиеся XML-теги"):
        load_config(tmp_path / "config.yaml")


def test_load_config_parses_manual_card_brief(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog:\n"
        "  report_category_ids: [2,6,7]\n"
        "  exclude_title_patterns: []\n"
        "  manual_card_brief:\n"
        "    \"НС-1\": \"Тихий, мощный, Wi-Fi\"\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.catalog.manual_card_brief == {"НС-1": "Тихий, мощный, Wi-Fi"}


def test_load_config_parses_fully_manual_products(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog:\n"
        "  report_category_ids: [2,6,7]\n"
        "  exclude_title_patterns: []\n"
        "  manual_products:\n"
        "    manual-x:\n"
        "      brand: ROYAL CLIMA\n"
        "      title: RCI-GR28HN\n"
        "      series: GRIDA Inverter\n"
        "      btu: 9\n"
        "      price: 26550\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.catalog.manual_products["manual-x"]["price"] == 26550


def test_load_config_defaults_profile_to_conditioners_behavior(tmp_path):
    """Боевой config.yaml без секции profile: работает как раньше (oasis_db + серии)."""
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog: {report_category_ids: [2], exclude_title_patterns: []}\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.source == "oasis_db"
    assert cfg.grouping == "series"
    assert cfg.profile_name == ""


def test_load_config_parses_profile_section(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "profile: {name: wreaths, source: ritualb2b_site, grouping: per_item}\n"
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog: {report_category_ids: [], exclude_title_patterns: []}\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.profile_name == "wreaths"
    assert cfg.source == "ritualb2b_site"
    assert cfg.grouping == "per_item"


@pytest.mark.parametrize("grouping", ["items", "", 123])
def test_load_config_rejects_unknown_grouping(tmp_path, grouping):
    profile = tmp_path / "config.yaml"
    profile.write_text(
        "profile:\n"
        "  source: oasis_db\n"
        f"  grouping: {grouping!r}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Неизвестный режим группировки"):
        load_config(profile)


def test_load_config_rejects_unknown_source(tmp_path):
    profile = tmp_path / "config.yaml"
    profile.write_text(
        "profile: {source: oasis_typo, grouping: series}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Неизвестный источник товаров"):
        load_config(profile)


def test_load_config_rejects_none_sentinel_mixed_with_explicit_keys(tmp_path):
    profile = tmp_path / "config.yaml"
    profile.write_text(
        "catalog:\n"
        "  selected_series: ['__none__', 'price_xls|item|pricexls:one']\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="'__none__' допустим только как единственное значение",
    ):
        load_config(profile)


def test_load_config_accepts_none_sentinel_as_only_selection(tmp_path):
    profile = tmp_path / "config.yaml"
    profile.write_text(
        "catalog:\n"
        "  selected_series: ['__none__']\n",
        encoding="utf-8",
    )

    assert load_config(profile).selected_series == frozenset({"__none__"})


def test_carver_profile_retains_stock_but_excludes_generators():
    cfg = load_config(PROJECT_ROOT / "profiles" / "carver.yaml")
    assert cfg.source == "manual_only"
    assert cfg.source_options["excluded_device_types"] == ["Генераторы"]
    assert cfg.source_options["excluded_product_patterns"] == [r"(?i)\bгенератор\b"]
    from avito_bridge.ingest.sources import fetch_profile_offers
    assert fetch_profile_offers(cfg) == []
    assert cfg.pricing.default_markup_pct == 7
    assert cfg.pricing.rounding == "up_to_10"
    assert cfg.feed.max_active_ads == 24
    assert cfg.feed.min_active_ads == 24
    assert cfg.feed.max_drop_fraction == 0.0
    assert cfg.public_feed_path == "/opt/oasis/staticfiles/avito-feed-carver.xml"
    assert cfg.feed.base_tags["Category"] == "Ремонт и строительство"
    assert cfg.feed.base_tags["GoodsType"] == "Инструменты"
    assert cfg.feed.base_tags["ToolType"] == "Силовая, строительная техника и комплектующие"
    assert cfg.feed.base_tags["ToolSubType"] == "Устройства электропитания"
    assert cfg.feed.base_tags["DeviceType"] == "Генераторы"
    assert "GoodsSubType" not in cfg.feed.base_tags
    assert cfg.catalog.manual_photos == {}
    assert cfg.catalog.manual_price_override == {}

    expected_prices = {
        "PPG-2100IS-DUOMATIC": 33599,
        "PPG-2500IS": 29113,
        "PPG-3500IS-DUOMATIC": 38320,
        "PPG-3500IS": 33406,
        "PPG-3600I-PROMO": 19164,
        "PPG-3600I": 21560,
        "PPG-3900I": 23432,
        "PPG-4100IS": 42449,
        "PPG-4500I": 25201,
        "PPG-4500IS-DUOMATIC": 53727,
        "PPG-4500IS": 48372,
        "PPG-5100ISE": 50419,
        "PPG-5500I-DUOMATIC": 34475,
        "PPG-5500I": 30997,
        "PPG-9500IR": 61117,
        "PPG-10000IR": 78318,
        "PPG-15000IVR": 97724,
        "PPG-3600": 16597,
        "PPG-8000": 41738,
        "PPG-8000EM": 46199,
        "PPG-9000R": 52037,
        "PPG-9000E": 50467,
        "PPG-10000R": 56403,
        "PPG-10000EM": 56403,
    }
    assert {
        key: int(product["price"])
        for key, product in cfg.catalog.manual_products.items()
    } == expected_prices
    assert all(
        int(product["stock"]) > 0
        for product in cfg.catalog.manual_products.values()
    )
    assert all(
        product["photos"]
        for product in cfg.catalog.manual_products.values()
    )
    assert sum(
        len(product["photos"])
        for product in cfg.catalog.manual_products.values()
    ) == 74
    assert all(
        product["avito_tags"]["Model"] == product["series"]
        for product in cfg.catalog.manual_products.values()
    )
    assert all(
        "  " not in product["description"]
        for product in cfg.catalog.manual_products.values()
    )
    assert cfg.feed.vendor_map == {"CARVER": ""}
    assert "4,5/5,0 кВт" in (
        cfg.catalog.manual_products["PPG-5500I"]["description"]
    )
    assert (
        cfg.catalog.manual_products["PPG-5500I"]["avito_tags"]["RatedPower"]
        == "4.5"
    )
    assert (
        cfg.catalog.manual_products["PPG-5500I"]["avito_tags"]["MaximumPower"]
        == "5.0"
    )
    assert "220/400 В" in (
        cfg.catalog.manual_products["PPG-15000IVR"]["description"]
    )
    assert (
        cfg.catalog.manual_products["PPG-15000IVR"]["avito_tags"]["Voltage"]
        == "220 В / 380 В"
    )
    assert cfg.selected_series == frozenset(
        f"manual|item|manual:{key}" for key in expected_prices
    )


def test_appliances_profile_has_only_its_128_explicit_selections():
    cfg = load_config(PROJECT_ROOT / "profiles" / "appliances.yaml")

    assert len(cfg.selected_series) == 128
    assert "__none__" not in cfg.selected_series
    assert all(
        key.startswith("price_xls|item|pricexls:")
        for key in cfg.selected_series
    )
