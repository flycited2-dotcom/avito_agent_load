"""Экспорт ПОЛНОГО каталога профиля (все серии источника, не только опубликованные) для GUI
«Контент-студия». Запускается на VPS по SSH: `python -m avito_bridge.catalog_export
[--config profiles/x.yaml]` → JSON в stdout. Студия сливает этот вывод с локальным YAML профиля
(selected_series), чтобы показать таблицу «что есть в источнике» × «что публикуется»."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from avito_bridge.config import AppConfig, load_config
from avito_bridge.models import Offer
from avito_bridge.ingest.sources import get_source
from avito_bridge.catalog.series import group_by_series, group_per_item, SeriesGroup
from avito_bridge.pricing.pricing import compute_price
from avito_bridge.content.cards import has_card


def _member_json(m: Offer, cfg: AppConfig) -> dict:
    pr = compute_price(m, cfg.pricing)
    nc = m.supplier_sku.split(":", 1)[-1]
    return {"nc_code": nc, "btu_calc": m.btu_calc, "stock": m.stock,
            "price": pr.price, "price_ok": pr.ok, "forced": m.forced}


def _group_json(g: SeriesGroup, cfg: AppConfig) -> dict:
    representative_nc = g.representative.supplier_sku.split(":", 1)[-1]
    return {"key": g.key, "source": g.source, "brand": g.brand, "series": g.series,
            "category_id": g.category_id,
            "stock_total": sum(m.stock for m in g.members),
            "has_card": (has_card(g.representative, cfg.cards)
                         or bool((cfg.catalog.manual_photos or {}).get(representative_nc))),
            "forced": any(m.forced for m in g.members),
            "members": [_member_json(m, cfg) for m in g.members]}


def build_catalog_json(offers: list[Offer], cfg: AppConfig) -> dict:
    # та же группировка, что в боевом pipeline: строки таблицы студии = объявления фида
    groups = group_per_item(offers) if cfg.grouping == "per_item" else group_by_series(offers)
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "series": [_group_json(g, cfg) for g in groups]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Экспорт каталога профиля в JSON для GUI студии")
    ap.add_argument("--config", default="config/config.yaml",
                    help="путь к YAML профиля (default: боевой кондиционерный)")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    offers = get_source(cfg.source)(cfg)
    print(json.dumps(build_catalog_json(offers, cfg), ensure_ascii=False))


if __name__ == "__main__":
    main()
