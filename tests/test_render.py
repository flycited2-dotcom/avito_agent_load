from decimal import Decimal
from avito_bridge.models import Offer
from avito_bridge.content.render import render_content, ContentConfig

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
