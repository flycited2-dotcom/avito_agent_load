"""python -m avito_bridge.ready_price: проверка/импорт без публикации."""
import argparse
import json
from pathlib import Path

from .store import export_catalog, import_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--categories", type=Path, required=True)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    result = import_release(args.release, args.database, args.categories) if args.release else {}
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text(json.dumps(export_catalog(args.database), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 2 if result.get("status") == "quarantined" else 0


if __name__ == "__main__":
    raise SystemExit(main())
