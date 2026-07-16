import pytest
from avito_bridge.ingest.sources import get_source


def test_oasis_db_source_registered():
    fetch = get_source("oasis_db")
    assert callable(fetch)


def test_carver_xlsx_source_registered():
    assert callable(get_source("carver_xlsx"))


def test_unknown_source_raises_with_available_list():
    with pytest.raises(ValueError, match="oasis_db"):
        get_source("no_such_source")


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
