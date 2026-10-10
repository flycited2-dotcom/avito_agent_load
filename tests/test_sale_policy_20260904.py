from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace
import pytest

from avito_bridge.models import Offer
from avito_bridge.pricing.pricing import PricingConfig, compute_price
from avito_bridge.ingest.sources import _excluded_product


def policy():
    return PricingConfig(default_markup_pct=10, rounding="none", rules=[{
        "match": {"source": "rusklimat"}, "exclude_category_ids": [2, 6, 7],
        "markup_pct": 10, "post_markup_pct": 15,
    }])


@pytest.mark.parametrize("category", [15, 19, 21, 22, 24, 26, 27, 30, 37, 40, 46, 117, 118, 119, None])
def test_rusklimat_non_ac_surcharge_is_once_after_base_rounding(category):
    offer = Offer(supplier_sku="r:1", source="rusklimat", category_id=category, cost=Decimal("100.01"))
    result = compute_price(offer, policy())
    assert result.price == 128  # ceil(100.01 * 1.10) = 111; ceil(111 * 1.15) = 128
    assert result.markup_pct == 26.5
    assert compute_price(offer, policy()).price == result.price
    assert offer.cost == Decimal("100.01")


@pytest.mark.parametrize("category", [2, 6, 7])
def test_rusklimat_ac_not_changed(category):
    offer = Offer(supplier_sku="r:1", source="rusklimat", category_id=category, cost=10000)
    assert compute_price(offer, policy()).price == 11000


@pytest.mark.parametrize("source", ["breeze", "daichi", "jac", "manual", "pricexls", "carver_xlsx"])
def test_other_sources_not_changed(source):
    offer = Offer(supplier_sku="s:1", source=source, category_id=30, cost=10000)
    assert compute_price(offer, policy()).price == 11000


def test_rusklimat_manual_price_also_gets_surcharge():
    offer = Offer(supplier_sku="r:1", source="rusklimat", category_id=30, price_override=1001)
    assert compute_price(offer, policy()).price == 1152


@pytest.mark.parametrize("title,device,excluded", [
    ("Генератор CARVER PPG-8000", "", True),
    ("Бензиновый генератор OPTIMA", "", True),
    ("PPG-8000", "Генераторы", True),
    ("АВР CARVER ATS-10000", "", False),
    ("Блок автопуска для генератора", "", False),
    ("Парогенератор для уборки", "", False),
])
def test_generator_exclusion_preserves_accessories(title, device, excluded):
    cfg = SimpleNamespace(source_options={
        "excluded_product_patterns": [r"(?i)\bгенератор\b"],
        "excluded_device_types": ["Генераторы"],
    }, feed=SimpleNamespace(base_tags={}))
    offer = Offer(supplier_sku="g:1", source="manual", model=title,
                  attrs={"avito_tag:DeviceType": device})
    assert _excluded_product(offer, cfg) is excluded
