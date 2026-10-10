"""Источник «ritualb2b_site»: каталог сайта ritualb2b.ru (венки/корзинки).

АКТУАЛЬНЫЕ данные — это ДВА запроса, как делает сам сайт:
1. products.js (`var PRODUCTS = [...];`) — базовый каталог; схема подтверждена
   живым файлом 2026-07-15 (tests/fixtures/ritualb2b_products.js): sku, model,
   brand, group (venki|korzinki), size, price, stock, descShort/descLong, photo.
2. api/admin.php?action=products_overrides_public — переопределения из админки
   (SQLite): price_override, model_override, stock_override, active и т.д.
   ⚠️ Без этого шага цены УСТАРЕВШИЕ: владелец правит их в админке, а products.js
   не перегенерируется (инцидент 2026-07-16: 13 из 14 цен «из потолка»).
   Декларация PRODUCT_OVERRIDES внутри products.js — всегда пустая, не источник.

Фото отдаёт оптимизатор сайта: /api/img.php?f=<имя>&w=1100 (проверено HEAD: 200,
image/jpeg) — этот URL и кладём в фид, Avito скачает."""
from __future__ import annotations
import json
import re
import time
from decimal import Decimal
from urllib.parse import urlsplit
from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

PRODUCTS_RE = re.compile(r"var\s+PRODUCTS\s*=\s*(\[.*?\])\s*;", re.S)
PHOTO_WIDTH = 1100   # «детальная карточка» по докам api/img.php сайта
OVERRIDES_PATH = "/api/admin.php?action=products_overrides_public"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_CATALOG_RESPONSE_BYTES = 10 * 1024 * 1024


def parse_products_js(text: str) -> list[dict]:
    m = PRODUCTS_RE.search(text)
    if not m:
        raise ValueError("products.js: не найдена декларация 'var PRODUCTS = [...];' — "
                         "сайт сменил формат каталога?")
    return json.loads(m.group(1))


def parse_overrides(payload: str | dict) -> dict[str, dict]:
    """Ответ products_overrides_public → {sku: override}. Формат зафиксирован живым
    ответом 2026-07-16 (tests/fixtures/ritualb2b_overrides.json)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not data.get("ok"):
        raise ValueError("products_overrides_public: ответ без ok=true — админка сменила формат?")
    return data.get("overrides") or {}


def _photo_list(raw) -> list[str]:
    """photos_override хранится строкой ('["a.png"]' или через запятую/перенос)."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
    except (ValueError, TypeError):
        pass
    return [s.strip() for s in re.split(r"[\n,]+", str(raw)) if s.strip()]


def apply_overrides(products: list[dict], overrides: dict[str, dict]) -> list[dict]:
    """Повторяет логику каталога index.html: active==0 скрывает товар,
    price_override>0 заменяет цену, непустые *_override — поля, photos_override — фото."""
    out = []
    for p in products:
        ov = overrides.get(p.get("sku")) or {}
        active = ov.get("active")
        if active not in (None, "") and int(active) == 0:
            continue
        p = dict(p)
        if ov.get("price_override") and int(ov["price_override"]) > 0:
            p["price"] = int(ov["price_override"])
        for ov_key, key in (("model_override", "model"), ("brand_override", "brand"),
                            ("stock_override", "stock"), ("size_override", "size"),
                            ("desc_long_override", "descLong")):
            if ov.get(ov_key):
                p[key] = ov[ov_key]
        photos = _photo_list(ov.get("photos_override"))
        if photos:
            p["photos"] = photos
        out.append(p)
    return out


def offers_from_products(
    items: list[dict],
    base_url: str,
    manual_photos: dict | None = None,
    manual_price_override: dict | None = None,
) -> list[Offer]:
    manual_photos = manual_photos or {}
    manual_price_override = manual_price_override or {}
    offers = []
    for p in items:
        sku = str(p["sku"])
        if manual_photos.get(sku):
            photos = [str(manual_photos[sku])]
        else:
            names = p.get("photos") or ([p["photo"]] if p.get("photo") else [])
            photos = [f"{base_url}/api/img.php?f={n}&w={PHOTO_WIDTH}" for n in names]
        source_price = manual_price_override.get(sku, p["price"])
        offers.append(Offer(
            supplier_sku=f"ritualb2b:{sku}",
            source="ritualb2b",
            brand=(p.get("brand") or "").strip(),
            model=(p.get("model") or "").strip(),
            category_id=None,
            cost=Decimal(str(source_price)),
            stock=1 if p.get("stock") == "in_stock" else 0,
            photos=photos,
            series=None,
            attrs={"desc_long": p.get("descLong") or "",
                   "desc_short": p.get("descShort") or "",
                   "group": p.get("group") or "",
                   "size": p.get("size") or ""},
            price_override=(
                Decimal(str(manual_price_override[sku]))
                if sku in manual_price_override else None
            ),
        ))
    return offers


def _get_text(url: str, headers: dict[str, str], *, attempts: int = 3) -> str:
    """Read one required catalog endpoint with bounded transient retries."""
    import httpx

    timeout = httpx.Timeout(30, connect=10)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = httpx.get(url, timeout=timeout, headers=headers)
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError as exc:
                        raise ValueError(
                            "Catalog response has an invalid Content-Length"
                        ) from exc
                    if declared_size > MAX_CATALOG_RESPONSE_BYTES:
                        raise ValueError(
                            "Catalog response exceeds the 10 MiB safety limit"
                        )
                if len(response.content) > MAX_CATALOG_RESPONSE_BYTES:
                    raise ValueError(
                        "Catalog response exceeds the 10 MiB safety limit"
                    )
                return response.text
            response.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                else None
            )
            if status is not None and status not in RETRYABLE_STATUS_CODES:
                raise
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    if last_error is None:  # defensive: attempts is an internal positive constant
        raise RuntimeError("Catalog request was not attempted")
    raise last_error


def _validated_base_url(value: str) -> str:
    """Restrict the production adapter to its one trusted HTTPS origin."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "ritualb2b.ru"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "ritualb2b_site: site_base_url must be exactly "
            "https://ritualb2b.ru"
        )
    return "https://ritualb2b.ru"


def fetch_ritualb2b(cfg: AppConfig) -> list[Offer]:
    """Живой путь: products.js + оверрайды админки (ОБА запроса обязательны — без
    оверрайдов уедут устаревшие цены, лучше упасть и оставить прошлый фид).
    base_url можно переопределить в профиле (catalog.site_base_url)."""
    base = _validated_base_url(
        cfg.catalog.site_base_url or "https://ritualb2b.ru"
    )
    headers = {"User-Agent": "avito-bridge/1.0"}
    products_text = _get_text(f"{base}/products.js", headers)
    overrides_text = _get_text(f"{base}{OVERRIDES_PATH}", headers)
    items = apply_overrides(
        parse_products_js(products_text),
        parse_overrides(overrides_text),
    )
    return offers_from_products(
        items,
        base_url=base,
        manual_photos=cfg.catalog.manual_photos,
        manual_price_override=cfg.catalog.manual_price_override,
    )
