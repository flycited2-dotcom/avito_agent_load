"""Safely combine independent Avito XML feeds for one Autoload account."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from lxml import etree

from avito_bridge.feed.writer import write_atomic


def merge_feeds(paths: list[Path], target: Path) -> int:
    """Merge Ad entries, rejecting malformed inputs and duplicate IDs."""
    if not paths:
        raise ValueError("At least one input feed is required")

    root = etree.Element("Ads", formatVersion="3", target="Avito.ru")
    seen_ids: set[str] = set()
    count = 0
    for path in paths:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        try:
            source_root = etree.parse(str(source)).getroot()
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ValueError(f"Invalid Avito feed {source}: {exc}") from exc
        if source_root.tag != "Ads":
            raise ValueError(f"Invalid Avito feed root in {source}: {source_root.tag!r}")

        for ad in source_root.findall("Ad"):
            ad_id = (ad.findtext("Id") or "").strip()
            if not ad_id:
                raise ValueError(f"Ad without Id in {source}")
            if ad_id in seen_ids:
                raise ValueError(f"Duplicate Avito Id while merging feeds: {ad_id}")
            seen_ids.add(ad_id)
            root.append(deepcopy(ad))
            count += 1

    if not count:
        raise ValueError("Merged Avito feed would be empty")
    document = etree.tostring(root, encoding="unicode", pretty_print=True)
    write_atomic('<?xml version="1.0" encoding="UTF-8"?>\n' + document, target)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge independent Avito XML feeds")
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    count = merge_feeds(args.input, args.output)
    print(f"ads={count} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
