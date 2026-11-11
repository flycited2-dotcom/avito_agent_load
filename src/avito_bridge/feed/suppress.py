"""CLI filter for persistent manual Avito exclusions."""
from __future__ import annotations

import argparse
from pathlib import Path

from avito_bridge.avito.manual_stop import suppress_feed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the manual Avito stop list")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop", required=True, type=Path)
    args = parser.parse_args(argv)
    removed, remaining = suppress_feed(args.input, args.output, args.stop)
    print(f"manual_stops={removed} ads={remaining} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
