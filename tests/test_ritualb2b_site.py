"""Адаптер ritualb2b_site. Фикстуры — РЕАЛЬНЫЕ ответы ritualb2b.ru:
products.js (2026-07-15, 55 товаров) и products_overrides_public (2026-07-16,
49 переопределений из админки — актуальные цены/названия живут ТАМ, не в products.js)."""
from decimal import Decimal
from pathlib import Path
from avito_bridge.ingest.ritualb2b_site import (parse_products_js, offers_from_products,
                                                parse_overrides, apply_overrides)

FIXTURE = (Path(__file__).parent / "fixtures" / "ritualb2b_products.js").read_text(encoding="utf-8")
OVERRIDES_RAW = (Path(__file__).parent / "fixtures" / "ritualb2b_overrides.json").read_text(encoding="utf-8")


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


def test_parse_real_overrides():
    ov = parse_overrides(OVERRIDES_RAW)
    assert len(ov) == 49
    assert ov["korzinka-everest"]["price_override"] == 780
    assert ov["korzinka-nimfa"]["model_override"] == "Скорбящий ангел 40см"


def test_apply_overrides_semantics_match_site():
    # правила из index.html сайта: active==0 скрывает; price_override>0 заменяет;
    # model/stock/descLong/фото — переопределяются при непустом значении
    products = [
        {"sku": "a", "model": "Старое имя", "price": 3200, "stock": "in_stock",
         "photo": "old.png", "descLong": "старый текст", "group": "korzinki", "brand": ""},
        {"sku": "b", "model": "Скрытый", "price": 100, "stock": "in_stock",
         "photo": "b.png", "descLong": "т", "group": "venki", "brand": ""},
        {"sku": "c", "model": "Без правок", "price": 500, "stock": "in_stock",
         "photo": "c.png", "descLong": "т", "group": "venki", "brand": ""},
    ]
    overrides = {
        "a": {"price_override": 780, "model_override": "Новое имя", "stock_override": "out_of_stock",
              "desc_long_override": "новый текст", "photos_override": '["new.png"]'},
        "b": {"active": 0},
    }
    merged = apply_overrides(products, overrides)
    assert [p["sku"] for p in merged] == ["a", "c"]     # b скрыт (active=0)
    a = merged[0]
    assert a["price"] == 780
    assert a["model"] == "Новое имя"
    assert a["stock"] == "out_of_stock"
    assert a["descLong"] == "новый текст"
    assert a["photos"] == ["new.png"]
    assert merged[1]["price"] == 500                    # без оверрайда — как было


def test_price_override_zero_and_null_keep_base_price():
    products = [{"sku": "a", "model": "А", "price": 500, "stock": "in_stock",
                 "photo": "a.png", "descLong": "т", "group": "venki", "brand": ""}]
    assert apply_overrides(products, {"a": {"price_override": 0}})[0]["price"] == 500
    assert apply_overrides(products, {"a": {"price_override": None}})[0]["price"] == 500


def test_offers_from_real_fixtures_get_admin_prices_not_products_js():
    # сквозняк на реальных фикстурах: цена «Эвереста» = 780 из админки, а не 3200 из products.js
    items = apply_overrides(parse_products_js(FIXTURE), parse_overrides(OVERRIDES_RAW))
    offers = {o.supplier_sku: o for o in offers_from_products(items, base_url="https://ritualb2b.ru")}
    assert offers["ritualb2b:korzinka-everest"].cost == Decimal("780")
    assert offers["ritualb2b:korzinka-nimfa"].model == "Скорбящий ангел 40см"


def test_photos_override_replaces_photo_url():
    items = apply_overrides(parse_products_js(FIXTURE), parse_overrides(OVERRIDES_RAW))
    offers = {o.supplier_sku: o for o in offers_from_products(items, base_url="https://ritualb2b.ru")}
    avrora = offers["ritualb2b:venok-avrora"]           # у Авроры photos_override в фикстуре
    assert avrora.photos == ["https://ritualb2b.ru/api/img.php?f=venok_2026-05-02_001.png&w=1100"]
