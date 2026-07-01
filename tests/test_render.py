from decimal import Decimal
from avito_bridge.models import Offer
from avito_bridge.content.render import render_content, render_series, ContentConfig
from avito_bridge.catalog.series import group_by_series

CFG = ContentConfig(title_max=50, description_max=7000, stop_words=["звоните"])


def _o(brand="Ballu", model="Olympio Edge BSO-07HN8", btu=7, cat=2, attrs=None):
    return Offer(supplier_sku="s", source="rusklimat", brand=brand, model=model,
                 category_id=cat, btu_calc=btu, attrs=attrs or {"Холод, кВт": "2.05"},
                 cost=Decimal("1"), retail_ref=None, stock=1, photos=[], series="Olympio Edge",
                 content_hash="h")


def test_title_has_brand_and_within_limit():
    c = render_content(_o(), CFG)
    assert "Ballu" in c.title
    assert len(c.title) <= 50


def test_title_truncated_when_too_long():
    c = render_content(_o(model="X" * 80), CFG)
    assert len(c.title) <= 50


def test_description_includes_specs_and_no_stopwords():
    c = render_content(_o(attrs={"Холод, кВт": "2.05", "звоните": "сейчас"}), CFG)
    assert "2.05" in c.description
    assert "звоните" not in c.description.lower()


def test_description_within_limit():
    c = render_content(_o(attrs={f"k{i}": "v" * 100 for i in range(200)}), CFG)
    assert len(c.description) <= 7000


def test_render_series_price_table_dedup_by_size():
    def mk(sku, btu, cost):
        return Offer(supplier_sku=sku, source="breeze", brand="Ballu", model=f"Olympio {btu}",
                     category_id=2, btu_calc=btu, attrs={}, cost=Decimal(cost), retail_ref=None,
                     stock=1, photos=[], series="Olympio (Olimpio)", content_hash="h")
    g = group_by_series([mk("b:1", 7, "10000"), mk("b:2", 7, "15000"), mk("b:3", 9, "12000")])[0]
    prices = {"b:1": 11090, "b:2": 16090, "b:3": 13090}
    c = render_series(g, prices, ContentConfig(title_max=50, description_max=7000, stop_words=[]))
    assert c.description.count("7000 BTU") == 1     # один размер — одна строка
    assert "11 090" in c.description                # минимальная цена для 7000
    assert "16 090" not in c.description            # дороже 7000 — не показываем
    assert "9000 BTU" in c.description
    assert "(Olimpio)" not in c.title               # скобочная латиница убрана из заголовка


def test_smart_title_fixes_caps():
    from avito_bridge.content.render import _smart_title
    assert _smart_title("FUNAI SENSEI 2.0 Inverter") == "Funai Sensei 2.0 Inverter"
    assert _smart_title("HIGH LIFE PRIORITY CLASS 2.0") == "High Life Priority Class 2.0"
    assert _smart_title("Hisense CITY DC Inverter") == "Hisense City DC Inverter"
    assert _smart_title("LG PROCOOL DUAL Inverter") == "LG Procool Dual Inverter"
    assert _smart_title("AC ELECTRIC PRO") == "AC Electric Pro"
    assert _smart_title("Midea Парамаунт") == "Midea Парамаунт"      # уже норм — не трогаем


def test_card_brief_is_clean_series_text():
    from avito_bridge.content.render import card_brief

    def mk(sku, btu):
        return Offer(supplier_sku=sku, source="breeze", brand="Midea", model=f"Paramount {btu}",
                     category_id=2, btu_calc=btu, attrs={"Трубопровод, мм": "9.52"},
                     cost=Decimal("1"), retail_ref=None, stock=1, photos=[],
                     series="Парамаунт", content_hash="h")
    g = group_by_series([mk("a:1", 7), mk("a:2", 12), mk("a:3", 18)])[0]
    t = card_brief(g)
    assert t.startswith("Midea Парамаунт")
    assert "7 / 12 / 18 тыс. BTU" in t                  # размерный ряд серии на карточке
    assert "Классическая (вкл/выкл)" in t               # не инвертор
    assert "9.52" not in t and "Трубопровод" not in t   # сырой ТТХ НЕ утекает на карточку


def test_render_series_website_link_only_for_selected():
    g = group_by_series([Offer(supplier_sku="b:1", source="breeze", brand="Ballu",
                               model="Olympio 7", category_id=2, btu_calc=7, attrs={},
                               cost=Decimal("10000"), retail_ref=None, stock=1, photos=[],
                               series="Olympio", content_hash="h")])[0]
    link = "Каталог: splithome.ru"
    on = ContentConfig(stop_words=[], website_link=link, website_link_keys=frozenset({g.key}))
    off = ContentConfig(stop_words=[], website_link=link, website_link_keys=frozenset())
    assert link in render_series(g, {"b:1": 11090}, on).description
    assert link not in render_series(g, {"b:1": 11090}, off).description    # серия не отмечена → без ссылки


def test_render_series_uses_description_override():
    g = group_by_series([Offer(supplier_sku="b:1", source="breeze", brand="Ballu",
                               model="Olympio 7", category_id=2, btu_calc=7, attrs={},
                               cost=Decimal("10000"), retail_ref=None, stock=1, photos=[],
                               series="Olympio", content_hash="h")])[0]
    cfg = ContentConfig(stop_words=[], descriptions={g.key: "Готовый текст про серию Olympio."})
    c = render_series(g, {"b:1": 11090}, cfg)
    assert c.description.startswith("Готовый текст про серию Olympio.")
    assert "11 090" in c.description                  # живая таблица цен дописана
    assert "Почему берут у нас" not in c.description   # автогенерация-футер не используется


def test_render_series_reinterprets_btu_on_price_inversion():
    # btu_calc=25 площадь-карта трактует как размер 7 (самый дешёвый), но цена 78290 — самая высокая.
    # Инверсия → трактуем btu как kBTU → 25000, порядок становится монотонным.
    def mk(sku, btu, cost):
        return Offer(supplier_sku=sku, source="breeze", brand="Z", model=f"PROGRESS {btu}",
                     category_id=2, btu_calc=btu, attrs={}, cost=Decimal(cost), retail_ref=None,
                     stock=1, photos=[], series="PROGRESS", content_hash="h")
    g = group_by_series([mk("z:1", 25, "74490"), mk("z:2", 12, "35290"), mk("z:3", 18, "60490")])[0]
    prices = {"z:1": 78290, "z:2": 37090, "z:3": 63590}
    c = render_series(g, prices, ContentConfig(title_max=50, description_max=7000, stop_words=[]))
    assert "7000 BTU" not in c.description           # 25 больше НЕ становится «семёркой»
    assert "25000 BTU" in c.description
    assert c.description.index("25000 BTU") > c.description.index("18000 BTU")   # порядок по размеру
