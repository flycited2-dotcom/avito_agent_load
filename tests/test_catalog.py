from decimal import Decimal
from avito_bridge.models import Offer
from avito_bridge.catalog.catalog import dedup_offers


def _o(sku, brand, model, cost, stock=1):
    return Offer(supplier_sku=sku, source=sku.split(":")[0], brand=brand, model=model,
                 category_id=2, btu_calc=7, attrs={}, cost=Decimal(cost), retail_ref=None,
                 stock=stock, photos=[], series=None, content_hash="h")


def test_dedup_keeps_cheapest_in_stock():
    a = _o("daichi:1", "Ballu", "X-07", "30000", stock=2)
    b = _o("rusklimat:2", "Ballu", "X-07", "28000", stock=1)   # тот же brand+model, дешевле
    out = dedup_offers([a, b])
    assert len(out) == 1 and out[0].supplier_sku == "rusklimat:2"


def test_dedup_prefers_in_stock_over_cheaper_oos():
    a = _o("daichi:1", "Ballu", "X-07", "30000", stock=3)
    b = _o("rusklimat:2", "Ballu", "X-07", "20000", stock=0)
    out = dedup_offers([a, b])
    assert out[0].supplier_sku == "daichi:1"


def test_dedup_distinct_models_kept():
    out = dedup_offers([_o("daichi:1", "Ballu", "X-07", "1"), _o("daichi:2", "Ballu", "X-09", "1")])
    assert len(out) == 2
