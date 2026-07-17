from decimal import Decimal
from avito_bridge.models import Offer
from avito_bridge.pricing.pricing import round_up_90, compute_price, PricingConfig


def _offer(cost, **kw):
    base = dict(supplier_sku="s", source="daichi", brand="B", model="M",
                category_id=2, btu_calc=7, attrs={}, cost=cost, retail_ref=None,
                stock=1, photos=[], series=None, content_hash="")
    base.update(kw)
    return Offer(**base)


def test_round_up_90_examples():
    assert round_up_90(27724.2) == 27790      # из референса
    assert round_up_90(27800) == 27890
    assert round_up_90(27890) == 27890        # уже …90 — не растёт
    assert round_up_90(27891) == 27990


def test_default_markup_5pct():
    cfg = PricingConfig(default_markup_pct=5, min_margin_abs=0, rounding="up_to_90", rules=[])
    r = compute_price(_offer(Decimal("26404")), cfg)
    assert r.ok and r.price == 27790          # 26404*1.05=27724.2 → 27790 (без пола маржи)


def test_min_margin_guard_raises_price():
    cfg = PricingConfig(default_markup_pct=5, min_margin_abs=3000, rounding="up_to_90", rules=[])
    r = compute_price(_offer(Decimal("10000")), cfg)   # +5%=10500 (маржа 500<3000) → 13000 → …90
    assert r.ok and r.min_margin_applied and r.price == 13090


def test_reject_when_cost_missing():
    cfg = PricingConfig(default_markup_pct=5, min_margin_abs=3000, rounding="up_to_90", rules=[])
    r = compute_price(_offer(None), cfg)
    assert r.ok is False and r.reason


def test_rule_override_by_category():
    cfg = PricingConfig(default_markup_pct=5, min_margin_abs=0, rounding="up_to_90",
                        rules=[{"match": {"category_id": 7}, "markup_pct": 30}])
    r = compute_price(_offer(Decimal("10000"), category_id=7), cfg)
    assert r.markup_pct == 30 and r.price == 13090   # 10000*1.30=13000 → 13090


def test_rounding_none_keeps_site_price_intact():
    # Профили, где источник отдаёт финальную розницу (ritualb2b): 2300 → 2300, не 2390
    cfg = PricingConfig(default_markup_pct=0, min_margin_abs=0, rounding="none", rules=[])
    r = compute_price(_offer(Decimal("2300"), source="ritualb2b", category_id=None,
                             btu_calc=None), cfg)
    assert r.ok and r.price == 2300


def test_carver_markup_rounds_up_to_10():
    cfg = PricingConfig(default_markup_pct=7, min_margin_abs=0,
                        rounding="up_to_10", rules=[])
    result = compute_price(
        _offer(Decimal("22786"), source="carver_xlsx", category_id=None,
               btu_calc=None),
        cfg,
    )
    assert result.price == 24390


def test_round_up_to_100_is_not_up_to_90():
    cfg = PricingConfig(default_markup_pct=0, min_margin_abs=0,
                        rounding="up_to_100", rules=[])
    assert compute_price(_offer(Decimal("12491")), cfg).price == 12500


def test_unknown_rounding_mode_is_rejected():
    cfg = PricingConfig(default_markup_pct=0, min_margin_abs=0,
                        rounding="typo", rules=[])
    try:
        compute_price(_offer(Decimal("10000")), cfg)
    except ValueError as exc:
        assert "Неизвестный режим округления" in str(exc)
    else:
        raise AssertionError("unknown rounding mode must fail")
