"""Адаптер ritualb2b_site. Фикстура — РЕАЛЬНЫЙ products.js с ritualb2b.ru
(скачан 2026-07-15, 55 товаров: венки + корзинки)."""
from decimal import Decimal
from pathlib import Path
from avito_bridge.ingest.ritualb2b_site import parse_products_js, offers_from_products

FIXTURE = (Path(__file__).parent / "fixtures" / "ritualb2b_products.js").read_text(encoding="utf-8")


def test_parse_real_products_js():
    items = parse_products_js(FIXTURE)
    assert len(items) == 55
    first = items[0]
    assert first["sku"] == "venok-avrora"
    assert first["model"] == "Венок «Аврора»"
    assert first["price"] == 2300
    assert first["photo"].endswith(".png")


def test_offers_have_photo_urls_and_stock():
    items = parse_products_js(FIXTURE)
    offers = offers_from_products(items, base_url="https://ritualb2b.ru")
    assert len(offers) == 55
    o = offers[0]
    assert o.supplier_sku == "ritualb2b:venok-avrora"
    assert o.source == "ritualb2b"
    assert o.model == "Венок «Аврора»"
    assert o.cost == Decimal("2300")      # цена сайта = розничная; наценку решает pricing профиля
    assert o.stock == 1                   # in_stock → публикуем
    assert o.photos == ["https://ritualb2b.ru/api/img.php?f=ritual_2026-04-28_20-21-30_010.png&w=1100"] \
        or o.photos[0].startswith("https://ritualb2b.ru/api/img.php?f=")
    # descLong сайта — готовый текст объявления; кладём в attrs для рендера профиля
    assert "Венок «Аврора»" in o.attrs["desc_long"]
    assert o.attrs["group"] in ("venki", "korzinki")


def test_out_of_stock_items_get_zero_stock():
    items = [{"sku": "x", "model": "Тест", "price": 100, "stock": "out_of_stock",
              "photo": "a.png", "descLong": "t", "group": "venki", "brand": ""}]
    offers = offers_from_products(items, base_url="https://ritualb2b.ru")
    assert offers[0].stock == 0
