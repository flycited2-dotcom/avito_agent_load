from __future__ import annotations
from avito_bridge.models import RawProduct

CRIMEA_QUERY = """
SELECT p.source, p.nc_code, b.title AS brand, p.title, p.series, p.category_id,
       p.btu_calc, p.price_wholesale, s.price_base, s.quantity AS crimea_qty,
       (SELECT i.url FROM catalog_productimage i
        WHERE i.product_id = p.id ORDER BY i."order" LIMIT 1) AS image_url
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


def build_query_params(crimea: str, cats: list[int], deny: list[str]) -> dict:
    return {"crimea": crimea, "cats": cats, "deny": deny}


def row_to_raw(row: dict) -> RawProduct:
    img = row.get("image_url")
    return RawProduct(
        source=row["source"], nc_code=row.get("nc_code"), brand=row.get("brand"),
        title=row.get("title") or "", series=row.get("series"),
        category_id=row.get("category_id"), btu_calc=row.get("btu_calc"),
        price_wholesale=row.get("price_wholesale"), price_base=row.get("price_base"),
        stock_qty=int(row.get("crimea_qty") or 0),
        image_urls=[img] if img else [], tech={},
    )


def fetch_raw_products(dsn: dict, crimea: str, cats: list[int], deny: list[str]) -> list[RawProduct]:
    """Боевой путь (Фаза 0). Покрыт интеграционно при дымовом прогоне, не в юнит-тестах."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(host=dsn["host"], port=dsn["port"], dbname=dsn["dbname"],
                            user=dsn["user"], password=dsn["password"])
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(CRIMEA_QUERY, build_query_params(crimea, cats, deny))
            return [row_to_raw(r) for r in cur.fetchall()]
    finally:
        conn.close()
