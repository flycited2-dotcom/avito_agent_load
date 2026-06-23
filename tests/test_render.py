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
