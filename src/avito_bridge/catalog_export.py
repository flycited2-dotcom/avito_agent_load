"""Экспорт ПОЛНОГО каталога (все серии из БД, не только опубликованные) для GUI «Контент-студия».
Запускается на VPS по SSH: `python -m avito_bridge.catalog_export` → JSON в stdout.
Студия сливает этот вывод с локальным config.yaml (selected_series), чтобы показать таблицу
«что есть в БД» × «что публикуется» и дать переключить публикацию галочкой."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from decouple import config
from avito_bridge.config import AppConfig, load_config
from avito_bridge.models import Offer
from avito_bridge.ingest import collect_offers
from avito_bridge.ingest.oasis_db import fetch_raw_products
from avito_bridge.catalog.series import group_by_series, SeriesGroup
from avito_bridge.pricing.pricing import compute_price
from avito_bridge.content.cards import has_card


def _member_json(m: Offer, cfg: AppConfig) -> dict:
    pr = compute_price(m, cfg.pricing)
    nc = m.supplier_sku.split(":", 1)[-1]
    return {"nc_code": nc, "btu_calc": m.btu_calc, "stock": m.stock,
            "price": pr.price, "price_ok": pr.ok, "forced": m.forced}


def _group_json(g: SeriesGroup, cfg: AppConfig) -> dict:
    return {"key": g.key, "source": g.source, "brand": g.brand, "series": g.series,
            "category_id": g.category_id,
            "stock_total": sum(m.stock for m in g.members),
            "has_card": has_card(g.representative, cfg.cards),
            "forced": any(m.forced for m in g.members),
            "members": [_member_json(m, cfg) for m in g.members]}


def build_catalog_json(offers: list[Offer], cfg: AppConfig) -> dict:
    groups = group_by_series(offers)
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "series": [_group_json(g, cfg) for g in groups]}


def main() -> None:
    cfg = load_config(Path("config/config.yaml"))
    dsn = {"host": config("DB_HOST", "localhost"), "port": config("DB_PORT", "5432"),
           "dbname": config("DB_NAME"), "user": config("DB_USER"), "password": config("DB_PASSWORD")}
    raw = fetch_raw_products(dsn, "Симферополь", cfg.catalog.report_category_ids,
                             cfg.catalog.exclude_title_patterns,
                             force_include=cfg.catalog.force_include,
                             manual_photos=cfg.catalog.manual_photos)
    offers = collect_offers(raw, Path(config("JAC_STOCK_JSON", "")), cfg.catalog, lambda nc: None)
    print(json.dumps(build_catalog_json(offers, cfg), ensure_ascii=False))


if __name__ == "__main__":
    main()
