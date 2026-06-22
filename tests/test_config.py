from avito_bridge.config import load_config


def test_load_config_parses_cities_and_pricing(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "cities:\n"
        "  - {id: simferopol, name: Симферополь, avito_location: Симферополь}\n"
        "pricing: {rounding: up_to_90, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed: {max_active_ads: 200}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: []}\n"
        "catalog: {report_category_ids: [2,6,7], exclude_title_patterns: ['%мульти%'], crimea_warehouse: Симферополь}\n",
        encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.cities[0].avito_location == "Симферополь"
    assert cfg.pricing.default_markup_pct == 5
    assert cfg.feed.max_active_ads == 200
    assert cfg.catalog.report_category_ids == [2, 6, 7]
