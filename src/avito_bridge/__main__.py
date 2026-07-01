from __future__ import annotations
from pathlib import Path
from decouple import config
from avito_bridge.config import load_config
from avito_bridge.ingest import collect_offers
from avito_bridge.ingest.oasis_db import fetch_raw_products
from avito_bridge.orchestrator.pipeline import run_cycle


def main():
    cfg = load_config(Path("config/config.yaml"))
    dsn = {"host": config("DB_HOST", "localhost"), "port": config("DB_PORT", "5432"),
           "dbname": config("DB_NAME"), "user": config("DB_USER"), "password": config("DB_PASSWORD")}
    deny = cfg.catalog.exclude_title_patterns
    raw = fetch_raw_products(dsn, crimea="Симферополь",
                             cats=cfg.catalog.report_category_ids, deny=deny,
                             force_include=cfg.catalog.force_include)
    jac_path = Path(config("JAC_STOCK_JSON", "/opt/splithub_api_telegram/data/jac_stock_latest.json"))
    offers = collect_offers(raw, jac_path, cfg.catalog, breez_base_lookup=lambda nc: None)
    result = run_cycle(lambda: offers, cfg, feed_path=Path("feed_out/feed.xml"),
                       state_path=Path("state/state.db"))
    print(f"offers_in={result.offers_in} ads_built={result.ads_built} skipped={result.skipped}")


if __name__ == "__main__":
    main()
