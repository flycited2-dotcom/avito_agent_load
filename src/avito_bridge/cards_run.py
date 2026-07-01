"""CLI автогенерации карточек: собрать серии → поставить задачи в очередь фотоагента и
забрать готовые карточки в avito-cards/. Запускается по таймеру (throttle через FOTOGEN_PER_RUN).

  python -m avito_bridge.cards_run
"""
from __future__ import annotations
import json
from pathlib import Path
from decouple import config
from avito_bridge.config import load_config
from avito_bridge.ingest import collect_offers
from avito_bridge.ingest.oasis_db import fetch_raw_products
from avito_bridge.catalog.series import group_by_series
from avito_bridge.cards_pipeline import FotogenConfig, CardJobStore, run_once


def main():
    cfg = load_config(Path("config/config.yaml"))
    dsn = {"host": config("DB_HOST", "localhost"), "port": config("DB_PORT", "5432"),
           "dbname": config("DB_NAME"), "user": config("DB_USER"), "password": config("DB_PASSWORD")}
    raw = fetch_raw_products(dsn, "Симферополь", cfg.catalog.report_category_ids,
                             cfg.catalog.exclude_title_patterns,
                             force_include=cfg.catalog.force_include)
    offers = collect_offers(raw, Path(config("JAC_STOCK_JSON", "")), cfg.catalog, lambda nc: None)
    groups = group_by_series(offers)
    if cfg.selected_series:                     # карточки для отмеченных серий (+ forced)
        groups = [g for g in groups if g.key in cfg.selected_series
                  or any(getattr(m, "forced", False) for m in g.members)]
    groups = [g for g in groups if g.key not in cfg.cards.supplier_photo_series]   # эти — на фото поставщика
    modes_path = Path(config("FOTOGEN_MODES_JSON", "config/card_modes.json"))
    modes = json.loads(modes_path.read_text(encoding="utf-8")) if modes_path.exists() else {}
    fcfg = FotogenConfig(
        api_url=config("FOTOGEN_API_URL"), token=config("FOTOGEN_API_TOKEN"),
        chat_id=int(config("FOTOGEN_CHAT_ID", "1264067528")),
        queue_db=config("FOTOGEN_QUEUE_DB"), output_dir=config("FOTOGEN_OUTPUT_DIR"),
        cards_dir=cfg.cards.dir, mode=config("FOTOGEN_MODE", "conditioner"), modes=modes,
        per_run=int(config("FOTOGEN_PER_RUN", "8")),
        max_pending=int(config("FOTOGEN_MAX_PENDING", "15")),
        max_total=int(config("FOTOGEN_MAX_TOTAL", "100000")))
    store = CardJobStore(Path("state/card_jobs.db"))
    submitted, published = run_once(groups, fcfg, store)
    print(f"cards: series={len(groups)} submitted={submitted} published={published}")


if __name__ == "__main__":
    main()
