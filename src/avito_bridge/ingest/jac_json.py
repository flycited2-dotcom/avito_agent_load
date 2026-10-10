from __future__ import annotations
import json
import os
import time
from urllib.parse import quote
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


def _photo_map(path: Path, base_url: str) -> dict[tuple[str, str], list[str]]:
    """Load optional brand/series photos produced by the JAC scraper."""
    mapping_path = path.with_name("jac_photos_latest.json")
    if not mapping_path.is_file():
        return {}
    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    for brand, series_map in payload.items():
        if not isinstance(series_map, dict):
            continue
        for series, raw_values in series_map.items():
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            photos: list[str] = []
            for value in values:
                value = str(value or "").strip()
                if not value:
                    continue
                if value.startswith(("http://", "https://")):
                    photos.append(value)
                elif base_url:
                    photos.append(f"{base_url.rstrip('/')}/{quote(value)}")
            if photos:
                result[(str(brand).strip().casefold(), str(series).strip().casefold())] = photos
    return result


def load_jac_offers(
    path: Path,
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    future_skew_seconds: int = DEFAULT_FUTURE_SKEW_SECONDS,
    photo_base_url: str = "https://splithome.ru/static/jac-photos",
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
    photos_by_series = _photo_map(path, photo_base_url)
    offers: list[Offer] = []
    for r in rows:
        attrs = r.get("attributes", {}) or {}
        # Новый JAC-скрапер вынес категорию в поле верхнего уровня; старые
        # снимки хранили её внутри attributes. Поддерживаем обе схемы.
        category_text = attrs.get("категория") or r.get("category") or ""
        cat_id = _CAT_TEXT_TO_ID.get(str(category_text).strip())
        if cat_id is None:                       # мульти/аксессуары/неизвестное — пропуск
            continue
        if int(r.get("stock_qty") or 0) <= 0:
            continue
        cost = _to_decimal(r.get("price"))
        brand = str(r.get("brand") or "").strip()
        series = str(r.get("series") or attrs.get("серия") or "").strip()
        photos = photos_by_series.get((brand.casefold(), series.casefold()), [])
        raw = RawProduct(source="jac", nc_code=r.get("article"), brand=brand,
                         title=r.get("name", ""), series=series or None, category_id=cat_id,
                         btu_calc=None, price_wholesale=cost, stock_qty=int(r["stock_qty"]),
                         image_urls=photos, tech={k: str(v) for k, v in attrs.items()})
        offers.append(Offer(
            supplier_sku=f"jac:{r.get('article')}", source="jac", brand=brand,
            model=r.get("name", ""), category_id=cat_id, btu_calc=None, attrs=raw.tech,
            cost=cost, retail_ref=_to_decimal(attrs.get("РРЦ")), stock=int(r["stock_qty"]),
            photos=photos, series=series or None, content_hash=content_hash(raw),
        ))
    return offers
