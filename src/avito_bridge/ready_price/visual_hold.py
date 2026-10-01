"""Keep visually incorrect Avito ads expired in the shared autoload feed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as XML

from defusedxml import ElementTree as ET

from avito_bridge import profile_publish as pp


def apply_visual_holds(feed: Path, registry: Path) -> dict:
    """Set DateEnd for exact reviewed IDs; never touch an unexpected product."""
    if not registry.is_file():
        return {"configured": 0, "present": 0, "changed": []}
    spec = json.loads(registry.read_text(encoding="utf-8"))
    if spec.get("version") != 1 or not isinstance(spec.get("entries"), dict):
        raise ValueError("Invalid visual hold registry")
    root = ET.parse(feed).getroot()
    if root.tag != "Ads":
        raise ValueError("Invalid Avito feed root")
    changed = []
    present = 0
    seen = set()
    for ad in root.findall("Ad"):
        aid = (ad.findtext("Id") or "").strip()
        if aid not in spec["entries"]:
            continue
        if aid in seen:
            raise ValueError(f"Duplicate held ad in feed: {aid}")
        seen.add(aid)
        entry = spec["entries"][aid]
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid visual hold entry: {aid}")
        title = (ad.findtext("Title") or "").casefold()
        model = str(entry.get("model") or "").casefold()
        if not model or model not in title:
            raise ValueError(f"Visual hold identity mismatch: {aid}")
        end = str(entry.get("date_end") or "")
        if not end.startswith("2026-") or "T" not in end:
            raise ValueError(f"Invalid visual hold expiry: {aid}")
        present += 1
        element = ad.find("DateEnd")
        if element is None:
            element = XML.SubElement(ad, "DateEnd")
        if element.text != end:
            element.text = end
            changed.append(aid)
    if changed:
        pp._write_atomic_bytes(feed, XML.tostring(root, encoding="utf-8", xml_declaration=True))
    return {"configured": len(spec["entries"]), "present": present, "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply_visual_holds(args.feed, args.registry), ensure_ascii=False))


if __name__ == "__main__":
    main()
