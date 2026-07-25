from __future__ import annotations
import argparse
from pathlib import Path
from avito_bridge import __version__
from avito_bridge.config import load_config
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.orchestrator.pipeline import run_cycle


def _resolve_runtime_path(value: str, bridge_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else bridge_root / path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сборка XML-фида Avito по профилю бизнеса")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--config", default="config/config.yaml",
                    help="путь к конфигу профиля (default: боевой кондиционерный)")
    ap.add_argument("--feed-path", help="override output path (candidate/dry-run)")
    ap.add_argument("--state-path", default="state/state.db")
    args = ap.parse_args(argv)
    cfg = load_config(Path(args.config))
    offers = fetch_profile_offers(cfg)
    bridge_root = cfg.bridge_root or Path.cwd()
    feed_path = _resolve_runtime_path(args.feed_path or cfg.feed_path, bridge_root)
    state_path = _resolve_runtime_path(args.state_path, bridge_root)
    result = run_cycle(
        lambda: offers,
        cfg,
        feed_path=feed_path,
        state_path=state_path,
    )
    label = cfg.profile_name or "default"
    print(f"profile={label} offers_in={result.offers_in} "
          f"ads_built={result.ads_built} skipped={result.skipped} "
          f"changed={result.changed} feed={feed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
