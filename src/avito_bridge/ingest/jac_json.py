from __future__ import annotations
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from avito_bridge.models import Offer, RawProduct
from avito_bridge.ingest.normalize import content_hash

DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
DEFAULT_FUTURE_SKEW_SECONDS = 5 * 60

_CAT_TEXT_TO_ID = {
    "Бытовые сплит-системы": 2,
    "Полупромышленные системы": 6,
}  # Мультисплит/Аксессуары намеренно не маппятся → отсев


def _to_decimal(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except Exception:
        return None


def load_jac_offers(
    path: Path,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    future_skew_seconds: int = DEFAULT_FUTURE_SKEW_SECONDS,
    now: float | None = None,
) -> list[Offer]:
    """Load a stock snapshot only while its filesystem timestamp is trustworthy."""
    path = Path(path)
    if not path.exists():
        return []
    if max_age_seconds <= 0 or future_skew_seconds < 0:
        raise ValueError("JAC stock freshness limits must be positive")
    with path.open("r", encoding="utf-8") as source:
        modified = os.fstat(source.fileno()).st_mtime
        current = time.time() if now is None else float(now)
        age = current - modified
        if age > max_age_seconds:
            raise ValueError(
                "JAC stock snapshot is stale: "
                f"{age:.0f}s old, maximum is {max_age_seconds}s"
            )
        if age < -future_skew_seconds:
            raise ValueError(
                "JAC stock snapshot timestamp is too far in the future: "
                f"{-age:.0f}s, allowed skew is {future_skew_seconds}s"
            )
        rows = json.load(source)
    offers: list[Offer] = []
    for r in rows:
        attrs = r.get("attributes", {}) or {}
        cat_id = _CAT_TEXT_TO_ID.get((attrs.get("категория") or "").strip())
        if cat_id is None:                       # мульти/аксессуары/неизвестное — пропуск
            continue
        if int(r.get("stock_qty") or 0) <= 0:
            continue
        cost = _to_decimal(r.get("price"))
        raw = RawProduct(source="jac", nc_code=r.get("article"), brand=r.get("brand"),
                         title=r.get("name", ""), series=None, category_id=cat_id,
                         btu_calc=None, price_wholesale=cost, stock_qty=int(r["stock_qty"]),
                         image_urls=[], tech={k: str(v) for k, v in attrs.items()})
        offers.append(Offer(
            supplier_sku=f"jac:{r.get('article')}", source="jac", brand=r.get("brand") or "",
            model=r.get("name", ""), category_id=cat_id, btu_calc=None, attrs=raw.tech,
            cost=cost, retail_ref=_to_decimal(attrs.get("РРЦ")), stock=int(r["stock_qty"]),
            photos=[], series=None, content_hash=content_hash(raw),
        ))
    return offers
