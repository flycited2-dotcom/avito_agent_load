import os
import json
import shutil
from pathlib import Path
from decimal import Decimal

import pytest

from avito_bridge.ingest.jac_json import load_jac_offers

FIX = Path(__file__).parent / "fixtures" / "jac_stock_sample.json"


def _fresh_fixture(tmp_path, now=2_000_000_000):
    target = tmp_path / "jac.json"
    shutil.copyfile(FIX, target)
    os.utime(target, (now, now))
    return target, now


def test_loads_only_conditioners_in_stock(tmp_path):
    path, now = _fresh_fixture(tmp_path)
    offers = load_jac_offers(path, now=now)
    skus = {o.supplier_sku for o in offers}
    assert "jac:MDV-AB-07" in skus            # бытовой сплит — взят
    assert "jac:MDV-MULTI-2" not in skus       # мультисплит — отсеян
    assert "jac:ACC-1" not in skus             # аксессуар — отсеян


def test_jac_cost_is_price(tmp_path):
    path, now = _fresh_fixture(tmp_path)
    o = next(
        o
        for o in load_jac_offers(path, now=now)
        if o.supplier_sku == "jac:MDV-AB-07"
    )
    assert o.cost == Decimal("42000")
    assert o.stock == 5 and o.source == "jac"


def test_jac_uses_scraper_series_and_optional_photo_map(tmp_path):
    now = 2_000_000_000
    stock = tmp_path / "jac_stock_latest.json"
    stock.write_text(json.dumps([{
        "article": "T-1", "name": "T-1", "brand": "THAICON",
        "series": "BALANCE INVERTER", "stock_qty": 2, "price": 10000,
        "attributes": {"категория": "Бытовые сплит-системы"},
    }]), encoding="utf-8")
    (tmp_path / "jac_photos_latest.json").write_text(json.dumps({
        "THAICON": {"BALANCE INVERTER": "balance.png"},
    }), encoding="utf-8")
    os.utime(stock, (now, now))

    offer = load_jac_offers(stock, now=now, photo_base_url="https://site/images") [0]

    assert offer.series == "BALANCE INVERTER"
    assert offer.photos == ["https://site/images/balance.png"]


def test_jac_accepts_updated_top_level_category(tmp_path):
    now = 2_000_000_000
    stock = tmp_path / "jac_stock_latest.json"
    stock.write_text(json.dumps([{
        "article": "M-1", "name": "M-1 / O-1", "brand": "MDV",
        "series": "INFINI INVERTER", "stock_qty": 3, "price": 20000,
        "category": "Бытовые сплит-системы", "attributes": {},
    }]), encoding="utf-8")
    os.utime(stock, (now, now))

    offers = load_jac_offers(stock, now=now)

    assert [offer.supplier_sku for offer in offers] == ["jac:M-1"]


def test_missing_file_returns_empty():
    assert load_jac_offers(Path("nope.json")) == []


@pytest.mark.parametrize(
    ("mtime_offset", "message"),
    [
        (-(24 * 60 * 60 + 1), "stale"),
        (5 * 60 + 1, "future"),
    ],
)
def test_rejects_stale_or_future_stock_snapshot(
    tmp_path, mtime_offset, message
):
    now = 2_000_000_000
    path = tmp_path / "jac.json"
    path.write_text("[]", encoding="utf-8")
    modified = now + mtime_offset
    os.utime(path, (modified, modified))

    with pytest.raises(ValueError, match=message):
        load_jac_offers(path, now=now)


@pytest.mark.parametrize(
    ("max_age", "future_skew"),
    [(0, 300), (-1, 300), (86400, -1)],
)
def test_rejects_invalid_freshness_configuration(
    tmp_path, max_age, future_skew
):
    path = tmp_path / "jac.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="freshness limits"):
        load_jac_offers(
            path,
            max_age_seconds=max_age,
            future_skew_seconds=future_skew,
        )
