"""Источник «ritualb2b_site»: каталог сайта ritualb2b.ru (венки/корзинки).

Каталог сайта — публичный products.js вида `var PRODUCTS = [...];` (+ отдельная
декларация PRODUCT_OVERRIDES в том же файле). Схема записи подтверждена живым
файлом 2026-07-15 (см. tests/fixtures/ritualb2b_products.js): sku, model, brand,
group (venki|korzinki), size, price (розница, ₽), stock (in_stock|...),
descShort/descLong (готовые тексты), benefits[], photo (имя файла).

Фото отдаёт оптимизатор сайта: /api/img.php?f=<имя>&w=1100 (проверено HEAD: 200,
image/jpeg) — этот URL и кладём в фид, Avito скачает."""
from __future__ import annotations
import json
import re
from decimal import Decimal
from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

PRODUCTS_RE = re.compile(r"var\s+PRODUCTS\s*=\s*(\[.*?\])\s*;", re.S)
PHOTO_WIDTH = 1100   # «детальная карточка» по докам api/img.php сайта


def parse_products_js(text: str) -> list[dict]:
    m = PRODUCTS_RE.search(text)
    if not m:
        raise ValueError("products.js: не найдена декларация 'var PRODUCTS = [...];' — "
                         "сайт сменил формат каталога?")
    return json.loads(m.group(1))


def offers_from_products(items: list[dict], base_url: str) -> list[Offer]:
    offers = []
    for p in items:
        photo = p.get("photo") or ""
        photos = ([f"{base_url}/api/img.php?f={photo}&w={PHOTO_WIDTH}"] if photo else [])
        offers.append(Offer(
            supplier_sku=f"ritualb2b:{p['sku']}",
            source="ritualb2b",
            brand=(p.get("brand") or "").strip(),
            model=(p.get("model") or "").strip(),
            category_id=None,
            cost=Decimal(str(p["price"])),
            stock=1 if p.get("stock") == "in_stock" else 0,
            photos=photos,
            series=None,
            attrs={"desc_long": p.get("descLong") or "",
                   "desc_short": p.get("descShort") or "",
                   "group": p.get("group") or "",
                   "size": p.get("size") or ""},
        ))
    return offers


def fetch_ritualb2b(cfg: AppConfig) -> list[Offer]:
    """Живой путь: скачать products.js с сайта. base_url можно переопределить
    в профиле (catalog.site_base_url) — напр. для стейджинга."""
    import httpx
    base = getattr(cfg.catalog, "site_base_url", "") or "https://ritualb2b.ru"
    r = httpx.get(f"{base}/products.js", timeout=30,
                  headers={"User-Agent": "avito-bridge/1.0"})
    r.raise_for_status()
    return offers_from_products(parse_products_js(r.text), base_url=base)
