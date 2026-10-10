"""Полностью ручные кондиционеры, которых нет в БД поставщиков.

Они хранятся в catalog.manual_products конфигурации и проходят тот же конвейер,
что товары Oasis. Цена считается финальной (price_override), а стабильный manual_id
становится частью supplier_sku и поэтому сохраняет Avito Id между публикациями.
"""
from __future__ import annotations
from decimal import Decimal
from math import isfinite
import re

from avito_bridge.config import AppConfig
from avito_bridge.models import Offer, RawProduct

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_AVITO_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_CATEGORIES = {2, 6, 7}


def _positive_number(value, field: str, manual_id: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"manual_products.{manual_id}.{field}: требуется число") from None
    if not isfinite(number) or number <= 0:
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
            forced=False,
        ))
    return rows


def _validated_specs(specs: dict | None):
    for manual_id, spec in (specs or {}).items():
        manual_id = str(manual_id).strip()
        if not _SAFE_ID.fullmatch(manual_id):
            raise ValueError(f"manual_products: небезопасный id {manual_id!r}")
        if not isinstance(spec, dict):
            raise ValueError(f"manual_products.{manual_id}: требуется объект полей")
        yield manual_id, spec


def _positive_int(value, field: str, manual_id: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"manual_products.{manual_id}.{field}: требуется целое число") from None
    if number <= 0 or not number.is_integer():
        raise ValueError(
            f"manual_products.{manual_id}.{field}: требуется целое число больше нуля")
    return int(number)


def _photo_list(spec: dict, manual_id: str) -> list[str]:
    raw = spec.get("photos")
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"manual_products.{manual_id}.photos: требуется список фотографий")
    photos = [str(value).strip() for value in raw if str(value).strip()]
    if not photos:
        raise ValueError(f"manual_products.{manual_id}.photos: требуется хотя бы одна фотография")
    return photos


def _string_map(value, manual_id: str, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"manual_products.{manual_id}.{field}: требуется объект полей")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        item = str(raw_value).strip()
        if key and item:
            result[key] = item
    return result


def _profile_tags(spec: dict, cfg: AppConfig, manual_id: str) -> dict[str, str]:
    group = str(spec.get("group") or "").strip()
    group_tags = (cfg.source_options or {}).get("group_tags") or {}
    tags = _string_map(group_tags.get(group), manual_id, "group_tags")
    tags.update(_string_map(spec.get("avito_tags"), manual_id, "avito_tags"))
    for tag in tags:
        if not _SAFE_AVITO_TAG.fullmatch(tag):
            raise ValueError(
                f"manual_products.{manual_id}.avito_tags: небезопасное имя тега {tag!r}")
    return tags


def _manual_description(spec: dict, tech: dict[str, str]) -> str:
    lines = [str(spec.get("title") or "").strip()]
    description = str(spec.get("description") or "").strip()
    if description:
        lines.extend(["", description])
    if tech:
        lines.extend(["", "Характеристики:"])
        lines.extend(f"• {key}: {value}" for key, value in tech.items())
    return "\n".join(lines).strip()


def _conditioner_category(spec: dict, manual_id: str) -> int:
    try:
        category_id = int(spec.get("category_id") or 2)
    except (TypeError, ValueError):
        raise ValueError(
            f"manual_products.{manual_id}.category_id: требуется целое число") from None
    if category_id not in _CATEGORIES:
        raise ValueError(
            f"manual_products.{manual_id}.category_id: допустимы {sorted(_CATEGORIES)}")
    return category_id


def build_manual_offers(specs: dict | None, cfg: AppConfig) -> list[Offer]:
    """Build profile-aware offers for products entered directly in Studio."""
    offers: list[Offer] = []
    conditioner = cfg.profile_name in {"", "conditioners"}
    for manual_id, spec in _validated_specs(specs):
        brand = str(spec.get("brand") or "").strip()
        title = str(spec.get("title") or "").strip()
        series = str(spec.get("series") or "").strip()
        group = str(spec.get("group") or "").strip()
        if not title or (not brand and cfg.profile_name != "wreaths"):
            raise ValueError(
                f"manual_products.{manual_id}: brand и title обязательны")
        if conditioner and not series:
            raise ValueError(
                f"manual_products.{manual_id}: series обязателен для кондиционера")

        category_id = _conditioner_category(spec, manual_id) if conditioner else None
        btu = _positive_number(spec.get("btu"), "btu", manual_id) if conditioner else None
        price = _positive_number(spec.get("price"), "price", manual_id)
        stock = _positive_int(spec.get("stock", 1), "stock", manual_id)
        photos = _photo_list(spec, manual_id)
        tech = _string_map(spec.get("tech"), manual_id, "tech")
        tags = _profile_tags(spec, cfg, manual_id)
        attrs = dict(tech)
        attrs.update({f"avito_tag:{key}": value for key, value in tags.items()})
        attrs["desc_long"] = _manual_description(spec, tech)

        offers.append(Offer(
            supplier_sku=f"manual:{manual_id}",
            source="manual",
            brand=brand,
            model=title,
            category_id=category_id,
            btu_calc=btu,
            attrs=attrs,
            cost=None,
            stock=stock,
            photos=photos,
            series=series or group or title,
            price_override=Decimal(str(price)),
            forced=False,
        ))
    return offers
