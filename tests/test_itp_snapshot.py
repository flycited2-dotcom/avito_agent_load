import json
from datetime import datetime, timezone
import pytest

from avito_bridge.ingest.itp_snapshot import build_itp_offers, load_itp_snapshot


def payload(generated_at="2026-09-17T05:00:00+00:00"):
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "warehouse": {"id": 16, "name": "Крым"},
        "positions": 1,
        "totalUnits": 2,
        "items": [{
            "sku": 10692094,
            "part": "Х-К ХМ-4319-101",
            "vendor": "Atlant",
            "name": "Встраиваемый холодильник Atlant ХМ-4319-101",
            "categoryExternalId": 9948,
            "categoryName": "Встраиваемые холодильники",
            "stock": 2,
            "supplierPrice": 35188.1,
            "retailPrice": 44000,
            "imageUrls": ["https://example.test/fridge.jpg"],
            "specifications": {"Общий объём": "245 л"},
        }],
    }


def test_build_itp_offer_preserves_exact_stock_price_images_and_tags():
    offers = build_itp_offers(payload(), {
        "category_tags": {
            "9948": {
                "GoodsType": "Для кухни",
                "ProductType": "Холодильники и морозильные камеры",
            },
        },
    })
    assert len(offers) == 1
    offer = offers[0]
    assert offer.supplier_sku == "itp:10692094"
    assert offer.stock == 2
    assert offer.price_override == 44000
    assert offer.photos == ["https://example.test/fridge.jpg"]
    assert offer.attrs["avito_tag:Vendor"] == "Atlant"
    assert offer.attrs["avito_tag:Model"] == "Х-К ХМ-4319-101"
    assert offer.attrs["avito_tag:GoodsType"] == "Для кухни"
    assert offer.attrs["Общий объём"] == "245 л"


def test_load_itp_snapshot_rejects_stale_file(tmp_path):
    source = tmp_path / "latest.json"
    source.write_text(json.dumps(payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        load_itp_snapshot(
            source,
            max_age_seconds=60,
            now=datetime(2026, 9, 17, 5, 2, tzinfo=timezone.utc),
        )


def test_itp_source_is_registered():
    from avito_bridge.ingest.sources import get_source
    assert callable(get_source("itp_snapshot"))
