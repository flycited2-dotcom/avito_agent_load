"""Адаптер price_xls (опт-прайс БытТехОпт). Фикстура — РЕАЛЬНЫЙ файл
input/priceopt_20260716.xls из excel-automation (1626 позиций, все «Под заказ»)."""
from decimal import Decimal
from pathlib import Path

from avito_bridge.feed.builder import FeedConfig, build_ads, build_feed_xml
from avito_bridge.ingest.price_xls import parse_price_xls, build_offers, _clean_model
from avito_bridge.models import City

FIXTURE = Path(__file__).parent / "fixtures" / "priceopt_sample.xls"

OPTS = {
    "selected_groups": ["Электрочайники", "Холодильники с нижней морозильной камерой"],
    "group_tags": {
        "Электрочайники": {"GoodsType": "Для кухни", "GoodsSubType": "Мелкая кухонная техника"},
        "Холодильники с нижней морозильной камерой":
            {"GoodsType": "Для кухни", "GoodsSubType": "Холодильники и морозильные камеры"},
    },
    "description_template": "{model}\n\nНовый, гарантия. Группа: {group}.",
}


def test_parse_real_price_xls():
    rows = parse_price_xls(FIXTURE)
    assert len(rows) == 1626
    first = rows[0]
    assert first["article"] == "00-00001698"
    assert first["group"] == "Ножи и кухонные аксессуары"
    assert first["brand"] == "CAPPELLO"
    assert first["price"] == 485
    assert first["stock_label"] == "Под заказ"
    groups = {r["group"] for r in rows}
    assert "Электрочайники" in groups and "Кондиционеры инверторные" in groups


def test_clean_model_strips_leading_article_and_trailing_junk():
    assert _clean_model("003544 Крышка CAPPELLO стекло/силикон с ручкой, 24см,") == \
        "Крышка CAPPELLO стекло/силикон с ручкой, 24см"
    assert _clean_model("Чайник BRAYER BR1023") == "Чайник BRAYER BR1023"


def test_build_offers_only_selected_groups_with_tags_and_description():
    offers = build_offers(parse_price_xls(FIXTURE), OPTS)
    groups = {o.series for o in offers}
    assert groups == set(OPTS["selected_groups"])       # кондиционеры и посуда не просочились
    o = offers[0]
    assert o.supplier_sku.startswith("pricexls:")
    assert o.source == "price_xls"
    assert o.cost > 0
    assert o.stock == 1                                  # «Под заказ» публикуем осознанно
    assert o.photos == []                                # фото в прайсе нет — блокер боевой публикации
    assert o.attrs["avito_tag:GoodsType"] == "Для кухни"
    assert o.attrs["desc_long"].startswith(o.model)
    assert "Группа: " in o.attrs["desc_long"]


def test_build_offers_series_carries_group_for_pricing_rules():
    # наценка по группам идёт через pricing.rules match {series: <группа>} — series обязан быть группой
    offers = build_offers(parse_price_xls(FIXTURE), OPTS)
    kettles = [o for o in offers if o.series == "Электрочайники"]
    fridges = [o for o in offers if o.series == "Холодильники с нижней морозильной камерой"]
    assert len(kettles) == 51 and len(fridges) == 23    # счёт из живого файла 2026-07-16


def test_build_offers_applies_manual_photo_and_final_price_by_article():
    rows = parse_price_xls(FIXTURE)
    article = next(r["article"] for r in rows if r["group"] == "Электрочайники")
    offers = build_offers(
        rows, OPTS,
        manual_photos={article: "https://splithome.ru/static/cf-cards/card.jpg"},
        manual_price_override={article: 4990},
    )
    offer = next(o for o in offers if o.supplier_sku == f"pricexls:{article}")
    assert offer.photos == ["https://splithome.ru/static/cf-cards/card.jpg"]
    assert offer.price_override == Decimal("4990")


def test_extra_tags_reach_feed_xml():
    offers = build_offers(parse_price_xls(FIXTURE), OPTS)[:1]
    o = offers[0]
    cities = [City(id="simferopol", name="Симферополь", avito_location="Республика Крым, Симферополь")]
    cfg = FeedConfig(base_tags={"Category": "Бытовая техника"})
    ads = build_ads(offers, cities, content={o.supplier_sku: ("Т", "Д")},
                    prices={o.supplier_sku: 1000}, cfg=cfg)
    xml = build_feed_xml(ads, cfg)
    assert "<GoodsType>Для кухни</GoodsType>" in xml
    assert "<GoodsSubType>Мелкая кухонная техника</GoodsSubType>" in xml
    assert "<Category>Бытовая техника</Category>" in xml
