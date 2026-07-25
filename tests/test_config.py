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


def test_carver_profile_publishes_confirmed_stock_only():
    cfg = load_config(PROJECT_ROOT / "profiles" / "carver.yaml")
    assert cfg.source_options["path"] == "runtime/carver/current.xlsx"
    assert cfg.pricing.default_markup_pct == 7
    assert cfg.pricing.rounding == "up_to_10"
    assert cfg.feed.max_active_ads == 23
    assert cfg.public_feed_path == "/opt/oasis/staticfiles/avito-feed-carver.xml"
    assert cfg.feed.base_tags["Category"] == "Ремонт и строительство"
    assert cfg.feed.base_tags["GoodsType"] == "Инструменты"
    assert cfg.feed.base_tags["ToolType"] == "Силовая, строительная техника и комплектующие"
    assert cfg.feed.base_tags["ToolSubType"] == "Устройства электропитания"
    assert cfg.feed.base_tags["DeviceType"] == "Генераторы"
    assert "GoodsSubType" not in cfg.feed.base_tags
    assert len(cfg.catalog.manual_photos) == 23
    assert len(cfg.selected_series) == 23
    assert cfg.catalog.manual_price_override == {
        "PPG-1900IS": 21945,
        "PPG-4000IS": 33946,
        "PPG-5100I": 29546,
        "PPG-6600ISR": 58069,
        "PPG-8100I": 45804,
        "PPG-9500IR": 56683,
        "PPG-15000IR": 79695,
        "PPG-15000IVR": 90629,
        "PPG-2000IS": 25971,
        "PPG-3900": 18095,
        "PPG-3100I": 21538,
        "PPG-3600I": 24453,
        "PPG-5100ISE": 57167,
        "PPG-6500": 41294,
        "PPG-6500AM": 41294,
        "PPG-6500E": 44385,
        "PPG-6500R": 46783,
        "PPG-9000E": 57222,
        "PPG-9000R": 58993,
        "PPG-10000E": 62029,
        "PPG-10000EM": 63943,
        "PPG-10000R": 63943,
        "PPG-13500VR": 83974,
    }
    assert all(key.startswith("carver_xlsx|item|carver:PPG-")
               for key in cfg.selected_series)


def test_appliances_profile_has_only_its_128_explicit_selections():
    cfg = load_config(PROJECT_ROOT / "profiles" / "appliances.yaml")

    assert len(cfg.selected_series) == 128
    assert "__none__" not in cfg.selected_series
    assert all(
        key.startswith("price_xls|item|pricexls:")
        for key in cfg.selected_series
    )
