from __future__ import annotations
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from avito_bridge.models import RawProduct, Offer


@dataclass
class CatalogFilter:
    report_category_ids: list[int]
    exclude_title_patterns: list[str]   # шаблоны вида "%мульти%" (ILIKE-семантика)


def _matches_like(title: str, pattern: str) -> bool:
    core = pattern.strip("%").lower()
    return core in (title or "").lower()


def is_conditioner(raw: RawProduct, flt: CatalogFilter) -> bool:
    if raw.category_id not in flt.report_category_ids:
        return False
    if not raw.btu_calc or raw.btu_calc <= 0:
        return False
    if any(_matches_like(raw.title, p) for p in flt.exclude_title_patterns):
        return False
    return True


def content_hash(raw: RawProduct) -> str:
    parts = [raw.source, raw.brand or "", raw.title, str(raw.btu_calc),
             str(sorted(raw.tech.items()))]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def to_offer(raw: RawProduct, cost: Decimal | None) -> Offer:
    return Offer(
        supplier_sku=f"{raw.source}:{raw.nc_code or raw.title}",
        source=raw.source, brand=raw.brand or "", model=raw.title,
        category_id=raw.category_id, btu_calc=raw.btu_calc, attrs=dict(raw.tech),
        cost=cost, retail_ref=None, stock=raw.stock_qty, photos=list(raw.image_urls),
        series=raw.series, content_hash=content_hash(raw),
    )
