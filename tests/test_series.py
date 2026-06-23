from decimal import Decimal
from avito_bridge.models import Offer
from avito_bridge.catalog.series import group_by_series, series_key


def _o(sku, brand, series, btu, model=None):
    return Offer(supplier_sku=sku, source="breeze", brand=brand, model=model or f"{series} {btu}",
                 category_id=2, btu_calc=btu, attrs={}, cost=Decimal("1"), retail_ref=None,
                 stock=1, photos=[], series=series, content_hash="h")


def test_groups_models_of_same_series():
    offers = [_o("b:3", "Ballu", "Olympio", 12), _o("b:1", "Ballu", "Olympio", 7),
              _o("b:2", "Ballu", "Olympio", 9)]
    groups = group_by_series(offers)
    assert len(groups) == 1
    g = groups[0]
    assert [m.btu_calc for m in g.members] == [7, 9, 12]      # сортировка по размеру
    assert g.representative.supplier_sku == "b:1"             # младший размер
    assert g.supplier_sku == "b:1"


def test_distinct_series_separate_groups():
    offers = [_o("b:1", "Ballu", "Olympio", 7), _o("d:1", "Daikin", "FTXB", 9)]
    assert len(group_by_series(offers)) == 2


def test_no_series_each_its_own_group():
    a = _o("x:1", "Ballu", "", 7, model="ABC")
    b = _o("x:2", "Ballu", "", 9, model="DEF")
    a.series = None
    b.series = None
    groups = group_by_series([a, b])
    assert len(groups) == 2


def test_series_key_case_insensitive():
    assert series_key(_o("b:1", "Ballu", "Olympio", 7)) == series_key(_o("b:2", "ballu", "olympio", 9))
