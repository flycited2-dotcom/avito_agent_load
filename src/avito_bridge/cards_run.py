"""CLI автогенерации карточек: собрать серии → поставить задачи в очередь фотоагента и
забрать готовые карточки в avito-cards/. Запускается по таймеру (throttle через FOTOGEN_PER_RUN).

  python -m avito_bridge.cards_run                  # по расписанию: whitelist + forced
  python -m avito_bridge.cards_run "some|series|key" # точечно ОДНА серия (из GUI Контент-студии,
                                                      # минуя whitelist — владелец явно попросил)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from decouple import config
from avito_bridge.config import load_config
from avito_bridge.ingest import collect_offers
from avito_bridge.ingest.oasis_db import fetch_raw_products
from avito_bridge.catalog.series import group_by_series, SeriesGroup
from avito_bridge.cards_pipeline import FotogenConfig, CardJobStore, run_once


def select_groups(groups: list[SeriesGroup], key: str | None, selected_series: frozenset,
                  supplier_photo_series: frozenset) -> list[SeriesGroup]:
    """key задан (точечный запрос из GUI) → только эта серия, МИНУЯ whitelist. Иначе — прежнее
    поведение по расписанию (whitelist selected_series + forced-товары). supplier_photo_series
    (серии на фото поставщика, без генерации) исключаются в любом случае."""
    if key:
        groups = [g for g in groups if g.key == key]
    elif selected_series:
        groups = [g for g in groups if g.key in selected_series
                  or any(getattr(m, "forced", False) for m in g.members)]
    return [g for g in groups if g.key not in supplier_photo_series]


def main() -> None:
    cfg = load_config(Path("config/config.yaml"))
    dsn = {"host": config("DB_HOST", "localhost"), "port": config("DB_PORT", "5432"),
           "dbname": config("DB_NAME"), "user": config("DB_USER"), "password": config("DB_PASSWORD")}
    raw = fetch_raw_products(dsn, "Симферополь", cfg.catalog.report_category_ids,
                             cfg.catalog.exclude_title_patterns,
                             force_include=cfg.catalog.force_include,
                             manual_photos=cfg.catalog.manual_photos)
    offers = collect_offers(raw, Path(config("JAC_STOCK_JSON", "")), cfg.catalog, lambda nc: None)
    groups = group_by_series(offers)
    key = sys.argv[1] if len(sys.argv) > 1 else None
    groups = select_groups(groups, key, cfg.selected_series, cfg.cards.supplier_photo_series)
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
