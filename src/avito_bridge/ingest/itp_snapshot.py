"""Read the exact I-T-P Crimea warehouse snapshot produced by web-store.

The producer writes the JSON atomically every 15 minutes.  This adapter is
read-only and refuses stale/future snapshots, so Avito can never keep selling
from an old successful run after the supplier API stops updating.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from pathlib import Path

from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024
MAX_SNAPSHOT_ITEMS = 2_000


def _timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("itp_snapshot: generatedAt is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("itp_snapshot: generatedAt must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise ValueError("itp_snapshot: generatedAt must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"itp_snapshot: {field} must be a positive integer")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"itp_snapshot: {field} must be a positive integer") from None
    if not isfinite(number) or number <= 0 or not number.is_integer():
        raise ValueError(f"itp_snapshot: {field} must be a positive integer")
    return int(number)


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValueError(f"itp_snapshot: {field} must be a positive number") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"itp_snapshot: {field} must be a positive number")
    return number


def load_itp_snapshot(
    path: str | Path,
    *,
    max_age_seconds: int = 1_800,
    max_future_skew_seconds: int = 300,
    now: datetime | None = None,
) -> dict:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError("itp_snapshot: file exceeds the 10 MiB safety limit")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"itp_snapshot: invalid JSON in {source}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("itp_snapshot: unsupported schemaVersion")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("itp_snapshot: invalid or oversized items list")

    generated = _timestamp(payload.get("generatedAt"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - generated).total_seconds()
    if age > max_age_seconds:
        raise ValueError(
            f"itp_snapshot: snapshot is stale ({int(age)}s old, maximum {max_age_seconds}s)"
        )
    if age < -max_future_skew_seconds:
        raise ValueError(
            "itp_snapshot: generatedAt is too far in the future "
            f"({int(-age)}s, maximum skew {max_future_skew_seconds}s)"
        )
    return payload


def _resolved_path(cfg: AppConfig) -> Path:
    configured = str((cfg.source_options or {}).get("path") or "").strip()
    if not configured:
        raise ValueError("itp_snapshot: profile.source_options.path is required")
    path = Path(configured).expanduser()
    if not path.is_absolute() and cfg.bridge_root:
        path = Path(cfg.bridge_root) / path
    return path


def _string_tags(value: object, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"itp_snapshot: {field} must be an object")
    return {
        str(key).strip(): str(item).strip()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def build_itp_offers(payload: dict, options: dict) -> list[Offer]:
    category_tags = options.get("category_tags") or {}
    if not isinstance(category_tags, dict):
        raise ValueError("itp_snapshot: category_tags must be an object")
    allowed_skus = {
        _positive_int(value, "selected_skus")
        for value in (options.get("selected_skus") or [])
    }
    template = str(options.get("description_template") or "").strip() or (
        "{name}\n\nНовый товар в заводской упаковке. "
        "Самовывоз в Симферополе или доставка по Крыму. "
        "Наличие и комплектацию уточняйте перед заказом."
    )
    offers: list[Offer] = []
    seen: set[int] = set()
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            raise ValueError("itp_snapshot: every item must be an object")
        sku = _positive_int(raw.get("sku"), "sku")
        if sku in seen:
            raise ValueError(f"itp_snapshot: duplicate SKU {sku}")
        seen.add(sku)
        if allowed_skus and sku not in allowed_skus:
            continue
        stock = _positive_int(raw.get("stock"), f"items[{sku}].stock")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"itp_snapshot: items[{sku}].name is required")
        vendor = str(raw.get("vendor") or "").strip()
        part = str(raw.get("part") or "").strip()
        category_id_raw = raw.get("categoryExternalId")
        category_id = int(category_id_raw) if category_id_raw is not None else None
        category_name = str(raw.get("categoryName") or "").strip()
        price = _positive_decimal(
            raw.get("retailPrice") or raw.get("supplierPrice"),
            f"items[{sku}].retailPrice",
        )
        photos = [
            str(url).strip()
            for url in (raw.get("imageUrls") or [])
            if str(url).strip().startswith(("http://", "https://"))
        ]
        tags = _string_tags(category_tags.get(str(category_id)), "category_tags")
        if options.get("add_vendor_tag", True) and vendor:
            tags.setdefault("Vendor", vendor)
        if options.get("add_model_tag", True) and part:
            tags.setdefault("Model", part)
        attrs = {
            "meta:category_name": category_name,
            "meta:category_external_id": str(category_id or ""),
            "meta:itp_snapshot_generated_at": str(payload.get("generatedAt") or ""),
            "desc_long": template.format(
                name=name, vendor=vendor, part=part, category=category_name, sku=sku
            ),
        }
        specifications = raw.get("specifications") or {}
        if not isinstance(specifications, dict):
            raise ValueError(f"itp_snapshot: items[{sku}].specifications must be an object")
        attrs.update({
            str(key).strip(): str(value).strip()
            for key, value in specifications.items()
            if str(key).strip() and str(value).strip()
        })
        attrs.update({f"avito_tag:{key}": value for key, value in tags.items()})
        offers.append(Offer(
            supplier_sku=f"itp:{sku}",
            source="itp",
            brand=vendor,
            model=name,
            category_id=category_id,
            attrs=attrs,
            cost=None,
            stock=stock,
            photos=photos,
            series=part or name,
            price_override=price,
        ))
    return offers


def fetch_itp_snapshot(cfg: AppConfig) -> list[Offer]:
    options = cfg.source_options or {}
    payload = load_itp_snapshot(
        _resolved_path(cfg),
        max_age_seconds=int(options.get("max_age_seconds", 1_800)),
        max_future_skew_seconds=int(options.get("max_future_skew_seconds", 300)),
    )
    return build_itp_offers(payload, options)
