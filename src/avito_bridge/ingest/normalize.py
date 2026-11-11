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
    excluded_sources: set[str] = None   # поставщики, полностью запрещённые к публикации
    force_include: dict = None           # {nc_code: цена} — принудительно в фид, минуя наличие БД
    manual_photos: dict = None           # {nc_code: url} — фото для товаров без фото в БД (ручное)
    manual_price_override: dict = None   # {nc_code: цена} — ручная цена для ЛЮБОГО товара (не только forced)
    manual_card_brief: dict = None       # {nc_code: текст} — ручное УТП для карточки, вместо card_brief()
    manual_products: dict = None         # {manual_id: поля} — товары, которых вообще нет в базе поставщика
    crimea_warehouse: str = "Симферополь"  # склад, остатки которого разрешено экспортировать
    site_base_url: str = ""              # базовый URL для web-источников профиля
    inverter_only_category_ids: set[int] = None
    heat_pump_threshold: int = -20
    category_tags: dict[int, dict] = None
    heat_pump_tags: dict = None
    category_labels: dict[int, str] = None
    supplier_photo_category_ids: set[int] = None
    include_jac_snapshot: bool = True


def _matches_like(title: str, pattern: str) -> bool:
    core = pattern.strip("%").lower()
    return core in (title or "").lower()


def is_conditioner(raw: RawProduct, flt: CatalogFilter) -> bool:
    if raw.category_id not in flt.report_category_ids:
        return False
    if raw.category_id in {2, 6, 7} and (not raw.btu_calc or raw.btu_calc <= 0):
        return False
    if any(_matches_like(raw.title, p) for p in flt.exclude_title_patterns):
        return False
    if raw.category_id in (flt.inverter_only_category_ids or set()):
        heat_pump = raw.is_heat_pump or (
            raw.heating_min_temp is not None
            and raw.heating_min_temp <= flt.heat_pump_threshold
        )
        if not raw.is_inverter and not heat_pump:
            return False
    return True


def content_hash(raw: RawProduct) -> str:
    parts = [raw.source, raw.brand or "", raw.title, str(raw.btu_calc),
             str(sorted(raw.tech.items()))]
    return hashlib.sha1(
        "|".join(parts).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def to_offer(raw: RawProduct, cost: Decimal | None) -> Offer:
    series, btu = raw.series, raw.btu_calc
    if raw.category_id not in {2, 6, 7}:
        series = (raw.tech or {}).get("Серия") or series
    if not (series and str(series).strip()):       # нет серии (rusklimat) → достаём из названия
        ps, pk = parse_model_title(raw.title, raw.brand)
        if ps:
            series = ps
        if pk and raw.source == "rusklimat":       # btu_calc у rusklimat недостоверен → берём из модель-кода
            btu = pk
    # Достоверный типоразмер: мощность охлаждения (кВт) → стандарт; fallback — btu к стандарту.
    size = derive_size(raw.cool_kw, btu, raw.category_id)
    attrs = dict(raw.tech)
    attrs.update({
        "meta:kind": raw.kind,
        "meta:is_inverter": "1" if raw.is_inverter else "0",
        "meta:is_heat_pump": "1" if raw.is_heat_pump else "0",
        "meta:heating_min_temp": (
            str(raw.heating_min_temp) if raw.heating_min_temp is not None else ""
        ),
    })
    return Offer(
        supplier_sku=f"{raw.source}:{raw.nc_code or raw.title}",
        source=raw.source, brand=raw.brand or "", model=raw.title,
        category_id=raw.category_id, btu_calc=size if size else btu, attrs=attrs,
        cost=cost, retail_ref=None, stock=raw.stock_qty, photos=list(raw.image_urls),
        series=series, content_hash=content_hash(raw),
        price_override=raw.price_override, forced=raw.forced,
    )
