"""CLI автогенерации карточек: собрать серии → поставить задачи в очередь фотоагента и
забрать готовые карточки в avito-cards/. Запускается по таймеру (throttle через FOTOGEN_PER_RUN).

  python -m avito_bridge.cards_run                  # по расписанию: whitelist + forced
  python -m avito_bridge.cards_run "some|series|key" # точечно ОДНА серия (из GUI Контент-студии,
                                                      # минуя whitelist — владелец явно попросил)
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from decouple import config
from avito_bridge.config import load_config
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.catalog.series import group_by_series, SeriesGroup
from avito_bridge.cards_pipeline import FotogenConfig, CardJobStore, run_once


def select_groups(groups: list[SeriesGroup], key: str | None, selected_series: frozenset,
                  supplier_photo_series: frozenset) -> list[SeriesGroup]:
    """key задан (точечный запрос из GUI) → только эта серия, МИНУЯ whitelist. Иначе — прежнее
    поведение по расписанию (строгий whitelist selected_series). supplier_photo_series
    (серии на фото поставщика, без генерации) исключаются в любом случае."""
    if key:
        groups = [g for g in groups if g.key == key]
    elif selected_series:
        groups = [g for g in groups if g.key in selected_series]
    return [g for g in groups if g.key not in supplier_photo_series]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate unique cards for selected series")
    parser.add_argument("key", nargs="?", help="one explicit series key")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--state-path", default="state/card_jobs.db")
    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config))
    if cfg.grouping != "series":
        raise ValueError("Card generation is supported only for series profiles")
    offers = fetch_profile_offers(cfg)
    groups = group_by_series(offers)
    groups = select_groups(
        groups, args.key, cfg.selected_series, cfg.cards.supplier_photo_series
    )
    bridge_root = cfg.bridge_root or Path.cwd()
    modes_path = Path(config("FOTOGEN_MODES_JSON", "config/card_modes.json"))
    if not modes_path.is_absolute():
        modes_path = bridge_root / modes_path
    modes = json.loads(modes_path.read_text(encoding="utf-8")) if modes_path.exists() else {}
    fcfg = FotogenConfig(
        api_url=config("FOTOGEN_API_URL"), token=config("FOTOGEN_API_TOKEN"),
        chat_id=int(config("FOTOGEN_CHAT_ID", "1264067528")),
        queue_db=config("FOTOGEN_QUEUE_DB"), output_dir=config("FOTOGEN_OUTPUT_DIR"),
        cards_dir=cfg.cards.dir, mode=config("FOTOGEN_MODE", "conditioner"), modes=modes,
        per_run=int(config("FOTOGEN_PER_RUN", "8")),
        max_pending=int(config("FOTOGEN_MAX_PENDING", "15")),
        max_total=int(config("FOTOGEN_MAX_TOTAL", "100000")))
    state_path = Path(args.state_path)
    if not state_path.is_absolute():
        state_path = bridge_root / state_path
    store = CardJobStore(state_path)
    submitted, published = run_once(groups, fcfg, store, manual_brief=cfg.catalog.manual_card_brief)
    print(f"cards: series={len(groups)} submitted={submitted} published={published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
