from decimal import Decimal
from avito_bridge.models import RawProduct
from avito_bridge.ingest.normalize import to_offer, is_conditioner, CatalogFilter

FLT = CatalogFilter(report_category_ids=[2, 6, 7],
                    exclude_title_patterns=["%мульти%", "%виброопор%"])


def _raw(**kw):
    base = dict(source="daichi", nc_code="N1", brand="Daichi", title="Сплит-система ABC 07",
                series="ABC", category_id=2, btu_calc=7, price_wholesale=Decimal("100"),
                price_base=None, stock_qty=3, image_urls=["u"], tech={"k": "v"})
    base.update(kw)
    return RawProduct(**base)


def test_is_conditioner_ok():
    assert is_conditioner(_raw(), FLT) is True


def test_filter_excludes_category_116():
    assert is_conditioner(_raw(category_id=116), FLT) is False


def test_filter_excludes_zero_btu():
    assert is_conditioner(_raw(btu_calc=0), FLT) is False


def test_heating_category_does_not_require_btu():
    flt = CatalogFilter(report_category_ids=[26], exclude_title_patterns=[])
    assert is_conditioner(_raw(category_id=26, btu_calc=None), flt) is True


def test_inverter_only_category_keeps_inverter_and_heat_pump():
    flt = CatalogFilter(
        report_category_ids=[2],
        exclude_title_patterns=[],
        inverter_only_category_ids={2},
        heat_pump_threshold=-20,
    )
    assert is_conditioner(_raw(is_inverter=True), flt) is True
    assert is_conditioner(_raw(is_inverter=False), flt) is False
    assert is_conditioner(
        _raw(is_inverter=False, heating_min_temp=-25), flt
    ) is True


def test_filter_excludes_multisplit_title():
    assert is_conditioner(_raw(title="Мульти сплит 3x"), FLT) is False


def test_to_offer_maps_fields():
    o = to_offer(_raw(), cost=Decimal("100"))
    assert o.supplier_sku == "daichi:N1"
    assert o.cost == Decimal("100")
    assert o.stock == 3 and o.photos == ["u"]
    assert o.content_hash  # непустой


def test_non_conditioner_uses_structured_series_and_meta():
    offer = to_offer(
        _raw(
            category_id=118,
            btu_calc=None,
            series=None,
            tech={"Серия": "Biliner", "Материал": "Биметалл"},
            is_inverter=False,
        ),
        cost=Decimal("100"),
    )
    assert offer.series == "Biliner"
    assert offer.attrs["meta:is_inverter"] == "0"


def test_content_hash_stable_and_sensitive():
    o1 = to_offer(_raw(), cost=Decimal("100"))
    o2 = to_offer(_raw(), cost=Decimal("100"))
    o3 = to_offer(_raw(title="Другое"), cost=Decimal("100"))
    assert o1.content_hash == o2.content_hash
    assert o1.content_hash != o3.content_hash
