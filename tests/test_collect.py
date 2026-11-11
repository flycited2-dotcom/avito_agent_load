from decimal import Decimal
from avito_bridge.models import RawProduct
from avito_bridge.ingest import collect_offers
from avito_bridge.ingest.normalize import CatalogFilter

FLT = CatalogFilter(report_category_ids=[2, 6, 7], exclude_title_patterns=["%мульти%"])


def test_collect_merges_db_and_jac(tmp_path):
    db_rows = [RawProduct(source="daichi", nc_code="N1", brand="Daichi", title="Сплит 07",
                          series=None, category_id=2, btu_calc=7,
                          price_wholesale=Decimal("10000"), price_base=None, stock_qty=2,
                          image_urls=["u"], tech={})]
    jac = tmp_path / "jac.json"
    jac.write_text('[{"article":"A1","name":"MDV 07","brand":"MDV","stock_qty":1,'
                   '"price":42000,"warehouse":"Крым","source":"jac_b2b",'
                   '"attributes":{"категория":"Бытовые сплит-системы"}}]', encoding="utf-8")
    offers = collect_offers(raw_db=db_rows, jac_path=jac, flt=FLT,
                            breez_base_lookup=lambda nc: None)
    skus = {o.supplier_sku for o in offers}
    assert "daichi:N1" in skus and "jac:A1" in skus
    daichi = next(o for o in offers if o.supplier_sku == "daichi:N1")
    assert daichi.cost == Decimal("10000")


def test_collect_can_use_only_current_site_database(tmp_path):
    flt = CatalogFilter(
        report_category_ids=[2],
        exclude_title_patterns=[],
        include_jac_snapshot=False,
    )
    row = RawProduct(
        source="daichi", nc_code="N1", brand="Daichi", title="Сплит 07",
        series="Eco", category_id=2, btu_calc=7,
        price_wholesale=Decimal("10000"), stock_qty=2, image_urls=["u"],
    )
    offers = collect_offers(
        [row], tmp_path / "missing-and-unused.json", flt, lambda _nc: None,
    )
    assert [offer.supplier_sku for offer in offers] == ["daichi:N1"]


def test_collect_snapshot_replaces_and_enriches_same_jac_sku(tmp_path):
    site = RawProduct(
        source="jac", nc_code="J1", brand="MDV", title="Полное название J1",
        series="INFINI INVERTER", category_id=2, btu_calc=9,
        price_wholesale=Decimal("30000"), stock_qty=1,
        image_urls=["https://site/photo.jpg"], tech={"Шум": "22"},
    )
    snapshot = tmp_path / "jac_stock_latest.json"
    snapshot.write_text(
        '[{"article":"J1","name":"J1","brand":"MDV",'
        '"series":"INFINI INVERTER","stock_qty":7,"price":25000,'
        '"category":"Бытовые сплит-системы","attributes":{}}]',
        encoding="utf-8",
    )

    offers = collect_offers([site], snapshot, FLT, lambda _nc: None)

    assert len(offers) == 1
    offer = offers[0]
    assert offer.stock == 7 and offer.cost == Decimal("25000")
    assert offer.model == "Полное название J1"
    assert offer.photos == ["https://site/photo.jpg"]
    assert offer.attrs["Шум"] == "22"


def test_collect_jac_respects_selected_profile_categories(tmp_path):
    flt = CatalogFilter(report_category_ids=[2], exclude_title_patterns=[])
    snapshot = tmp_path / "jac_stock_latest.json"
    snapshot.write_text(
        '[{"article":"P1","name":"P1","brand":"MDV",'
        '"series":"DUCT INVERTER","stock_qty":2,"price":30000,'
        '"category":"Полупромышленные системы","attributes":{}}]',
        encoding="utf-8",
    )

    assert collect_offers([], snapshot, flt, lambda _nc: None) == []
