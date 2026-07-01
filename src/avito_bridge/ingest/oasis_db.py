from __future__ import annotations
from decimal import Decimal
from avito_bridge.models import RawProduct
from avito_bridge.content.sizing import parse_kw

# Мощность ОХЛАЖДЕНИЯ (кВт) по nc_code — достоверный источник типоразмера. Имена ключей разные у
# поставщиков; берём ТОЛЬКО «холодопроизводительность/производительность охлаждения» (НЕ «потребляемая
# мощность» — это вход), исключаем макс/мин.
COOL_KW_QUERY = """
SELECT p.nc_code AS nc_code, ts.title AS title, pt.value AS value
FROM catalog_producttech pt
JOIN catalog_techspec ts ON ts.id = pt.spec_id
JOIN catalog_product p ON p.id = pt.product_id
WHERE p.nc_code = ANY(%(ncs)s)
  AND (ts.title ILIKE 'Холодопроизводительность (кВт)'
       OR ts.title ILIKE 'Холодопроизводительность, кВт%%ном.%%'
       OR ts.title ILIKE 'Холодопроизводительность, кВт'
       OR ts.title ILIKE 'Номинальная производительность охлаждения')
  AND ts.title NOT ILIKE '%%макс%%' AND ts.title NOT ILIKE '%%мин%%'
  AND ts.title NOT ILIKE '%%потреб%%';
"""

# Приоритет ключей мощности охлаждения (точнее → менее точное).
_KW_KEY_PRIORITY = ["холодопроизводительность (квт)", "холодопроизводительность, квт, ном.",
                    "холодопроизводительность, квт", "номинальная производительность охлаждения"]


def group_cool_kw(rows) -> dict[str, float]:
    """{nc_code: кВт} — по приоритету ключей берём самое достоверное значение охлаждения."""
    best: dict[str, tuple[int, float]] = {}
    for r in rows:
        nc = r.get("nc_code")
        kw = parse_kw(r.get("value"))
        title = (r.get("title") or "").strip().lower()
        if not nc or kw is None or kw <= 0:
            continue
        rank = _KW_KEY_PRIORITY.index(title) if title in _KW_KEY_PRIORITY else len(_KW_KEY_PRIORITY)
        if nc not in best or rank < best[nc][0]:
            best[nc] = (rank, kw)
    return {nc: kw for nc, (_, kw) in best.items()}

CRIMEA_QUERY = """
SELECT p.source, p.nc_code, b.title AS brand, p.title, p.series, p.category_id,
       p.btu_calc, p.price_wholesale, s.price_base, s.quantity AS crimea_qty,
       (SELECT array_agg(i.url ORDER BY i."order") FROM catalog_productimage i
        WHERE i.product_id = p.id) AS image_urls
FROM catalog_product p
JOIN stock_stock s ON s.product_id = p.id
LEFT JOIN catalog_brand b ON b.id = p.brand_id
WHERE p.is_active = TRUE
  AND s.warehouse = %(crimea)s
  AND s.quantity > 0
  AND p.category_id = ANY(%(cats)s)
  AND p.btu_calc > 0
  AND NOT (p.title ILIKE ANY(%(deny)s))
ORDER BY p.source, b.title NULLS LAST, p.title;
"""


# Технические характеристики по nc_code (порт _TECH_QUERY из референса + nc_code для группировки).
TECH_QUERY = """
SELECT p.nc_code AS nc_code, ts.title AS title, pt.value AS value
FROM catalog_producttech pt
JOIN catalog_techspec ts ON ts.id = pt.spec_id
JOIN catalog_product p ON p.id = pt.product_id
WHERE p.nc_code = ANY(%(ncs)s)
ORDER BY p.id, ts."order";
"""


# Принудительный набор по nc_code (минуя наличие/склад) — для force_include (товары под заказ).
FORCE_QUERY = """
SELECT p.source, p.nc_code, b.title AS brand, p.title, p.series, p.category_id,
       p.btu_calc, p.price_wholesale,
       (SELECT array_agg(i.url ORDER BY i."order") FROM catalog_productimage i
        WHERE i.product_id = p.id) AS image_urls
FROM catalog_product p
LEFT JOIN catalog_brand b ON b.id = p.brand_id
WHERE p.nc_code = ANY(%(ncs)s);
"""


def build_query_params(crimea: str, cats: list[int], deny: list[str]) -> dict:
    return {"crimea": crimea, "cats": cats, "deny": deny}


def group_tech_rows(rows, max_specs: int = 12) -> dict[str, dict]:
    """Сгруппировать строки ТТХ по nc_code → {nc: {title: value}} (пустые/дубли пропускаем)."""
    out: dict[str, dict] = {}
    for r in rows:
        nc = r.get("nc_code")
        title = (r.get("title") or "").strip()
        value = (str(r.get("value")) if r.get("value") is not None else "").strip()
        if not nc or not title or not value:
            continue
        d = out.setdefault(nc, {})
        if len(d) < max_specs and title not in d:
            d[title] = value
    return out


def row_to_raw(row: dict) -> RawProduct:
    imgs = [u for u in (row.get("image_urls") or []) if u][:10]   # все фото товара (Avito максимум 10)
    return RawProduct(
        source=row["source"], nc_code=row.get("nc_code"), brand=row.get("brand"),
        title=row.get("title") or "", series=row.get("series"),
        category_id=row.get("category_id"), btu_calc=row.get("btu_calc"),
        price_wholesale=row.get("price_wholesale"), price_base=row.get("price_base"),
        stock_qty=int(row.get("crimea_qty") or 0),
        image_urls=imgs, tech={},
    )


def fetch_raw_products(dsn: dict, crimea: str, cats: list[int], deny: list[str],
                       force_include: dict | None = None) -> list[RawProduct]:
    """Боевой путь (Фаза 0). Покрыт интеграционно при дымовом прогоне, не в юнит-тестах.
    force_include={nc_code: цена} — добрать эти товары минуя наличие БД (под заказ), с ручной ценой."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(host=dsn["host"], port=dsn["port"], dbname=dsn["dbname"],
                            user=dsn["user"], password=dsn["password"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(CRIMEA_QUERY, build_query_params(crimea, cats, deny))
            raws = [row_to_raw(r) for r in cur.fetchall()]
            have = {r.nc_code for r in raws}
            todo = [nc for nc in (force_include or {}) if nc not in have]
            if todo:                                   # принудительно добавленные товары (под заказ)
                cur.execute(FORCE_QUERY, {"ncs": todo})
                for row in cur.fetchall():
                    rp = row_to_raw(row)
                    rp.stock_qty = 1
                    rp.forced = True
                    price = force_include.get(rp.nc_code)
                    rp.price_override = Decimal(str(price)) if price else None
                    raws.append(rp)
            ncs = [r.nc_code for r in raws if r.nc_code]
            if ncs:
                cur.execute(TECH_QUERY, {"ncs": ncs})
                tech = group_tech_rows(cur.fetchall())
                cur.execute(COOL_KW_QUERY, {"ncs": ncs})
                cool = group_cool_kw(cur.fetchall())
                for r in raws:
                    if r.nc_code in tech:
                        r.tech = tech[r.nc_code]
                    r.cool_kw = cool.get(r.nc_code)
            return raws
    finally:
        conn.close()
