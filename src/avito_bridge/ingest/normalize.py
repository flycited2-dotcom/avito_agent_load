from __future__ import annotations
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from avito_bridge.models import RawProduct, Offer
from avito_bridge.ingest.title_parse import parse_model_title
from avito_bridge.content.sizing import derive_size


@dataclass
class CatalogFilter:
    report_category_ids: list[int]
    exclude_title_patterns: list[str]   # шаблоны вида "%мульти%" (ILIKE-семантика)
    force_include: dict = None           # {nc_code: цена} — принудительно в фид, минуя наличие БД


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
    series, btu = raw.series, raw.btu_calc
    if not (series and str(series).strip()):       # нет серии (rusklimat) → достаём из названия
        ps, pk = parse_model_title(raw.title, raw.brand)
        if ps:
            series = ps
        if pk and raw.source == "rusklimat":       # btu_calc у rusklimat недостоверен → берём из модель-кода
            btu = pk
    # Достоверный типоразмер: мощность охлаждения (кВт) → стандарт; fallback — btu к стандарту.
    size = derive_size(raw.cool_kw, btu, raw.category_id)
    return Offer(
        supplier_sku=f"{raw.source}:{raw.nc_code or raw.title}",
        source=raw.source, brand=raw.brand or "", model=raw.title,
        category_id=raw.category_id, btu_calc=size if size else btu, attrs=dict(raw.tech),
        cost=cost, retail_ref=None, stock=raw.stock_qty, photos=list(raw.image_urls),
        series=series, content_hash=content_hash(raw),
        price_override=raw.price_override, forced=raw.forced,
    )
