"""Полностью ручные кондиционеры, которых нет в БД поставщиков.

Они хранятся в catalog.manual_products конфигурации и проходят тот же конвейер,
что товары Oasis. Цена считается финальной (price_override), а стабильный manual_id
становится частью supplier_sku и поэтому сохраняет Avito Id между публикациями.
"""
from __future__ import annotations
from decimal import Decimal
import re

from avito_bridge.models import RawProduct

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_CATEGORIES = {2, 6, 7}


def _positive_number(value, field: str, manual_id: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"manual_products.{manual_id}.{field}: требуется число") from None
    if number <= 0:
        raise ValueError(f"manual_products.{manual_id}.{field}: значение должно быть больше нуля")
    return number


def build_manual_raw_products(specs: dict | None) -> list[RawProduct]:
    rows: list[RawProduct] = []
    for manual_id, spec in (specs or {}).items():
        manual_id = str(manual_id).strip()
        if not _SAFE_ID.fullmatch(manual_id):
            raise ValueError(f"manual_products: небезопасный id {manual_id!r}")
        if not isinstance(spec, dict):
            raise ValueError(f"manual_products.{manual_id}: требуется объект полей")
        brand = str(spec.get("brand") or "").strip()
        title = str(spec.get("title") or "").strip()
        series = str(spec.get("series") or title).strip()
        if not brand or not title or not series:
            raise ValueError(
                f"manual_products.{manual_id}: brand, title и series обязательны")
        category_id = int(spec.get("category_id") or 2)
        if category_id not in _CATEGORIES:
            raise ValueError(
                f"manual_products.{manual_id}.category_id: допустимы {sorted(_CATEGORIES)}")
        btu = _positive_number(spec.get("btu"), "btu", manual_id)
        price = _positive_number(spec.get("price"), "price", manual_id)
        photos = [str(p).strip() for p in (spec.get("photos") or []) if str(p).strip()]
        tech = {str(k): str(v) for k, v in (spec.get("tech") or {}).items() if str(v).strip()}
        rows.append(RawProduct(
            source="manual",
            nc_code=manual_id,
            brand=brand,
            title=title,
            series=series,
            category_id=category_id,
            btu_calc=btu,
            stock_qty=max(1, int(spec.get("stock") or 1)),
            image_urls=photos,
            tech=tech,
            price_override=Decimal(str(price)),
            forced=True,
        ))
    return rows
