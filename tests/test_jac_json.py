import os
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
