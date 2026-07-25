import json
import sys
from decimal import Decimal

from PIL import Image

from avito_bridge.models import Offer, City
from avito_bridge.pricing.pricing import PricingConfig
from avito_bridge.feed.builder import FeedConfig
from avito_bridge.content.render import ContentConfig
from avito_bridge.ingest.normalize import CatalogFilter
from avito_bridge.content.cards import CardConfig
from avito_bridge.config import AppConfig
from avito_bridge.catalog_export import build_catalog_json


def _cfg(cards=None):
    return AppConfig(
        cities=[City(id="simferopol", name="Симферополь", avito_location="Симферополь")],
        pricing=PricingConfig(default_markup_pct=5, min_margin_abs=0, rounding="up_to_90", rules=[]),
        feed=FeedConfig(max_active_ads=50, base_tags={}),
        content=ContentConfig(title_max=50, description_max=7000, stop_words=[]),
        catalog=CatalogFilter(report_category_ids=[2, 6, 7], exclude_title_patterns=[]),
        cards=cards or CardConfig())


def _offer(sku, series="Sensei 2.0", btu=7, stock=2, forced=False, cost="10000"):
    return Offer(supplier_sku=sku, source="breeze", brand="Funai", model="X",
                 category_id=2, btu_calc=btu, attrs={}, cost=Decimal(cost) if cost else None,
                 retail_ref=None, stock=stock, photos=["https://i/1.jpg"], series=series,
                 content_hash=sku, forced=forced)


def test_build_catalog_json_groups_by_series_with_price_and_stock():
    offers = [_offer("breeze:НС-1", btu=7, stock=2), _offer("breeze:НС-2", btu=9, stock=3)]
    data = build_catalog_json(offers, _cfg())
    assert len(data["series"]) == 1
    g = data["series"][0]
    assert g["brand"] == "Funai" and g["series"] == "Sensei 2.0"
    assert g["stock_total"] == 5
    assert g["has_card"] is False
    assert g["forced"] is False
    members = {m["nc_code"]: m for m in g["members"]}
    assert members["НС-1"]["price"] == 10590        # 10000*1.05=10500 → round_up_90=10590
    assert members["НС-1"]["price_ok"] is True


def test_build_catalog_json_exposes_member_cost_for_bulk_price_floor():
    offers = [_offer("breeze:НС-1", cost="10000"), _offer("breeze:НС-2", cost=None)]
    second = offers[1]
    second.price_override = Decimal("12990")

    members = build_catalog_json(offers, _cfg())["series"][0]["members"]

    assert members[0]["cost"] == 10000
    assert members[1]["cost"] is None


def test_build_catalog_json_rounds_fractional_cost_up_for_price_floor():
    member = build_catalog_json(
        [_offer("breeze:НС-1", cost="10000.50")], _cfg()
    )["series"][0]["members"][0]

    assert member["cost"] == 10001


def test_build_catalog_json_per_item_grouping():
    # Профили без серий (grouping: per_item — венки): каждый товар = своя строка каталога,
    # даже если серия/модель совпадают (кондиционерное схлопывание не должно сработать).
    cfg = _cfg()
    cfg.grouping = "per_item"
    offers = [_offer("ritualb2b:w-1"), _offer("ritualb2b:w-2")]
    data = build_catalog_json(offers, cfg)
    assert len(data["series"]) == 2
    assert all("|item|" in g["key"] for g in data["series"])


PROFILE_YAML = """\
profile:
  name: test-profile
  source: fake_source
  grouping: per_item
catalog:
  selected_series: []
"""


def test_main_reads_profile_config_and_dispatches_source(tmp_path, monkeypatch, capsys):
    # Студия зовёт catalog_export с --config <профиль>: источник берётся из profile.source,
    # а не хардкодом oasis (иначе селектор профилей в GUI показывал бы всем кондиционеры).
    from avito_bridge import catalog_export
    from avito_bridge.ingest import sources
    profile = tmp_path / "test.yaml"
    profile.write_text(PROFILE_YAML, encoding="utf-8")
    monkeypatch.setitem(sources.SOURCES, "fake_source",
                        lambda cfg: [_offer("x:1"), _offer("x:2")])
    monkeypatch.setattr(sys, "argv", ["catalog_export", "--config", str(profile)])
    catalog_export.main()
    data = json.loads(capsys.readouterr().out)
    assert len(data["series"]) == 2
    assert all("|item|" in g["key"] for g in data["series"])


def test_build_catalog_json_marks_forced_and_has_card(tmp_path):
    Image.new("RGB", (8, 8), "white").save(tmp_path / "НС-3.jpg", format="JPEG")
    cards = CardConfig(enabled=True, dir=str(tmp_path), exts=[".jpg"])
    o = _offer("rusklimat:НС-3", series="ACE-07", forced=True, cost=None)
    o.price_override = Decimal("18990")
    data = build_catalog_json([o], _cfg(cards=cards))
    g = data["series"][0]
    assert g["forced"] is True
    assert g["has_card"] is True
    assert g["members"][0]["price"] == 18990


def test_build_catalog_json_marks_manual_photo_as_card():
    cfg = _cfg()
    cfg.catalog.manual_photos = {"НС-1": "https://splithome.ru/static/manual-photos/НС-1.jpg"}
    data = build_catalog_json([_offer("breeze:НС-1")], cfg)
    assert data["series"][0]["has_card"] is True
