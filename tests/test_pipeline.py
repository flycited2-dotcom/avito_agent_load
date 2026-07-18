from decimal import Decimal
from avito_bridge.models import Offer, City
from avito_bridge.pricing.pricing import PricingConfig
from avito_bridge.feed.builder import FeedConfig
from avito_bridge.content.render import ContentConfig
from avito_bridge.ingest.normalize import CatalogFilter
from avito_bridge.content.cards import CardConfig
from avito_bridge.config import AppConfig
from avito_bridge.orchestrator.pipeline import run_cycle
from avito_bridge.ingest.manual_products import build_manual_offers


def _cfg():
    return AppConfig(
        cities=[City(id="simferopol", name="Симферополь", avito_location="Симферополь")],
        pricing=PricingConfig(default_markup_pct=5, min_margin_abs=0, rounding="up_to_90", rules=[]),
        feed=FeedConfig(max_active_ads=50, base_tags={"Category": "Бытовая электроника"}),
        content=ContentConfig(title_max=50, description_max=7000, stop_words=[]),
        catalog=CatalogFilter(report_category_ids=[2, 6, 7], exclude_title_patterns=[]),
        cards=CardConfig())


def _offer(sku):
    return Offer(supplier_sku=sku, source="daichi", brand="Ballu", model="X-07",
                 category_id=2, btu_calc=7, attrs={}, cost=Decimal("10000"), retail_ref=None,
                 stock=2, photos=["https://i/1.jpg"], series=None, content_hash=sku)


def test_run_cycle_writes_feed(tmp_path):
    feed_path = tmp_path / "feed.xml"
    result = run_cycle(
        offers_provider=lambda: [_offer("daichi:1")],
        cfg=_cfg(), feed_path=feed_path, state_path=tmp_path / "state.db")
    assert feed_path.exists()
    assert result.ads_built == 1
    assert "<Ads" in feed_path.read_text(encoding="utf-8")


def test_run_cycle_filters_selected_series(tmp_path):
    from avito_bridge.catalog.series import series_key
    a, b = _offer("daichi:1"), _offer("daichi:2")
    cfg = _cfg()
    cfg.selected_series = frozenset({series_key(a)})      # публикуем только серию a
    result = run_cycle(offers_provider=lambda: [a, b], cfg=cfg,
                       feed_path=tmp_path / "f.xml", state_path=tmp_path / "s.db")
    assert result.ads_built == 1


def test_run_cycle_requires_card_when_configured(tmp_path):
    cfg = _cfg()
    cfg.cards = CardConfig(enabled=True, dir=str(tmp_path / "nocards"), require_for_publish=True)
    result = run_cycle(offers_provider=lambda: [_offer("daichi:1")], cfg=cfg,
                       feed_path=tmp_path / "f.xml", state_path=tmp_path / "s.db")
    assert result.ads_built == 0 and result.skipped == 1   # нет карточки → не публикуем


def test_forced_offer_price_override_and_publish(tmp_path):
    from avito_bridge.pricing.pricing import compute_price
    o = _offer("rusklimat:НС-1690797")
    o.price_override = Decimal("18990")
    o.forced = True
    o.cost = None                                          # нет опта — не важно, ручная цена
    assert compute_price(o, _cfg().pricing).price == 18990
    cfg = _cfg()
    cfg.selected_series = frozenset({"что-то-другое"})     # forced публикуется, даже не в whitelist
    r = run_cycle(offers_provider=lambda: [o], cfg=cfg,
                  feed_path=tmp_path / "f.xml", state_path=tmp_path / "s.db")
    assert r.ads_built == 1


def test_run_cycle_supplier_photo_series_bypasses_card(tmp_path):
    from avito_bridge.catalog.series import series_key
    o = _offer("daichi:1")
    o.photos = ["https://i/1.jpg", "https://i/2.jpg", "https://i/3.jpg"]
    cfg = _cfg()
    cfg.cards = CardConfig(enabled=True, dir=str(tmp_path / "nocards"), require_for_publish=True,
                           supplier_photo_series=frozenset({series_key(o)}), max_images=10)
    feed = tmp_path / "f.xml"
    r = run_cycle(offers_provider=lambda: [o], cfg=cfg, feed_path=feed, state_path=tmp_path / "s.db")
    assert r.ads_built == 1                                 # без карточки, но опубликовано (фото поставщика)
    assert feed.read_text(encoding="utf-8").count("<Image ") == 3   # несколько фото


def test_fully_manual_offer_uses_uploaded_photo_without_generated_card(tmp_path):
    o = _offer("manual:manual-rc-gr28hn-a1b2c3d4")
    o.source = "manual"
    o.price_override = Decimal("26550")
    o.forced = True
    o.photos = ["https://splithome.ru/static/manual-photos/manual-x.jpg"]
    cfg = _cfg()
    cfg.cards = CardConfig(enabled=True, dir=str(tmp_path / "nocards"),
                           require_for_publish=True)
    feed = tmp_path / "manual.xml"
    result = run_cycle(lambda: [o], cfg, feed, tmp_path / "state.db")
    assert result.ads_built == 1
    assert "manual-x.jpg" in feed.read_text(encoding="utf-8")


def test_fully_manual_offer_without_photo_is_skipped(tmp_path):
    o = _offer("manual:manual-no-photo")
    o.source = "manual"
    o.price_override = Decimal("26550")
    o.forced = True
    o.photos = []
    cfg = _cfg()
    result = run_cycle(lambda: [o], cfg, tmp_path / "manual.xml", tmp_path / "state.db")
    assert result.ads_built == 0 and result.skipped == 1


def test_run_cycle_skips_unpriceable(tmp_path):
    bad = _offer("daichi:2")
    bad.cost = None
    result = run_cycle(offers_provider=lambda: [bad], cfg=_cfg(),
                       feed_path=tmp_path / "feed.xml", state_path=tmp_path / "s.db")
    assert result.ads_built == 0 and result.skipped == 1


def test_carver_manual_product_reaches_feed_with_profile_tags_and_description(tmp_path):
    cfg = _cfg()
    cfg.profile_name = "carver"
    cfg.grouping = "per_item"
    cfg.source_options = {}
    cfg.content = ContentConfig(
        title_max=50, description_max=7000, stop_words=[],
        description_attr="desc_long")
    cfg.feed = FeedConfig(
        max_active_ads=50,
        base_tags={"Category": "Ремонт и строительство", "DeviceType": "Генераторы"})
    offer = build_manual_offers({
        "manual-ppg-1900i": {
            "brand": "CARVER",
            "title": "Генератор CARVER PPG-1900i",
            "group": "generator",
            "price": 43200,
            "stock": 1,
            "photos": ["https://i/generator.jpg"],
            "description": "Компактный инверторный генератор.",
            "tech": {"Топливо": "Бензин"},
            "avito_tags": {"FuelType": "Бензин", "RatedPower": "1.7"},
        }
    }, cfg)[0]
    feed = tmp_path / "carver-manual.xml"

    result = run_cycle(lambda: [offer], cfg, feed, tmp_path / "state.db")
    xml = feed.read_text(encoding="utf-8")

    assert result.ads_built == 1
    assert "<FuelType>Бензин</FuelType>" in xml
    assert "<RatedPower>1.7</RatedPower>" in xml
    assert "Компактный инверторный генератор." in xml
    assert "Топливо: Бензин" in xml
    assert "https://i/generator.jpg" in xml
