"""One-time safe updater for a visible product-condition notice in Avito feeds."""
from __future__ import annotations

import argparse
from pathlib import Path

from lxml import etree

from avito_bridge.feed.writer import write_atomic


DEFAULT_NOTICE = (
    "Состояние: новое. Товар не использовался, в заводской упаковке."
)


def prepend_notice(source: Path, target: Path, notice: str = DEFAULT_NOTICE) -> tuple[int, int]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.parse(str(source), parser).getroot()
    if root.tag != "Ads":
        raise ValueError(f"Invalid Avito feed root in {source}: {root.tag!r}")
    changed = 0
    ads = root.findall("Ad")
    for ad in ads:
        description = ad.find("Description")
        if description is None:
            description = etree.SubElement(ad, "Description")
        old = (description.text or "").strip()
        if old.startswith(notice):
            continue
        description.text = f"{notice}\n\n{old}" if old else notice
        changed += 1
    document = etree.tostring(root, encoding="unicode", pretty_print=True)
    write_atomic('<?xml version="1.0" encoding="UTF-8"?>\n' + document, target)
    return changed, len(ads)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepend a visible condition notice")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--notice", default=DEFAULT_NOTICE)
    args = parser.parse_args(argv)
    changed, total = prepend_notice(args.input, args.output, args.notice)
    print(f"changed={changed} ads={total} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
