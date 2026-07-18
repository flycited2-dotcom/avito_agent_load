from __future__ import annotations
import argparse
from pathlib import Path
from avito_bridge.config import load_config
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.orchestrator.pipeline import run_cycle


def main():
    ap = argparse.ArgumentParser(description="Сборка XML-фида Avito по профилю бизнеса")
    ap.add_argument("--config", default="config/config.yaml",
                    help="путь к конфигу профиля (default: боевой кондиционерный)")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    offers = fetch_profile_offers(cfg)
    result = run_cycle(lambda: offers, cfg, feed_path=Path(cfg.feed_path),
                       state_path=Path("state/state.db"))
    label = cfg.profile_name or "default"
    print(f"profile={label} offers_in={result.offers_in} "
          f"ads_built={result.ads_built} skipped={result.skipped}")


if __name__ == "__main__":
    main()
