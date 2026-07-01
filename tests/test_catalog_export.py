from decimal import Decimal
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


def test_build_catalog_json_marks_forced_and_has_card(tmp_path):
    (tmp_path / "НС-3.jpg").write_bytes(b"x")
    cards = CardConfig(enabled=True, dir=str(tmp_path), exts=[".jpg"])
    o = _offer("rusklimat:НС-3", series="ACE-07", forced=True, cost=None)
    o.price_override = Decimal("18990")
    data = build_catalog_json([o], _cfg(cards=cards))
    g = data["series"][0]
    assert g["forced"] is True
    assert g["has_card"] is True
    assert g["members"][0]["price"] == 18990
