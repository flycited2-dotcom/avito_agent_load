"""Replace four reviewed wrong Avito photos with exact product photos.

Run on the VPS with --apply only after inspecting the four staged source images.
The shared feed is changed under the publisher lock and backed up first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from lxml import etree
from PIL import Image

from avito_bridge import profile_publish as pp


ROOT = Path("/opt/avito-bridge")
PUBLIC = Path("/opt/oasis/staticfiles")
FEED = PUBLIC / "avito-feed.xml"
REGISTRY = ROOT / "state/ready-price/visual-holds.json"
STAGED = ROOT / "state/ready-price/verified-images-20260923"
TARGETS = {
    "nikita-00-00000323": ("T1112", "white-toaster-verified.png"),
    "nikita-00-00000369": ("K17DWD", "gorenje-kettle-verified.png"),
    "nikita-00-00001238": ("KCG 0030-PR1", "korting-grinder-verified.png"),
    "nikita-00-00001381": ("MWO-B2502-M-BL", "ilmonte-microwave-verified.png"),
}


def repair(*, apply: bool = False) -> dict:
    with pp._exclusive_lock(ROOT / "state/profile-publish.lock"):
        doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
        if doc.get("version") != 1 or set(doc.get("entries", {})) != set(TARGETS):
            raise ValueError("Visual hold registry differs from reviewed target set")
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.parse(str(FEED), parser).getroot()
        if root.tag != "Ads":
            raise ValueError("Invalid Avito feed")
        by_id = {(ad.findtext("Id") or "").strip(): ad for ad in root.findall("Ad")}
        if not set(TARGETS) <= set(by_id):
            raise ValueError("Reviewed target missing from feed")
        operations = []
        report = []
        for aid, (model, filename) in TARGETS.items():
            ad = by_id[aid]
            if model.casefold() not in (ad.findtext("Title") or "").casefold():
                raise ValueError(f"Identity mismatch for {aid}")
            if ad.findtext("DateEnd") != doc["entries"][aid]["date_end"]:
                raise ValueError(f"Expected visual hold is missing for {aid}")
            source = STAGED / filename
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                if min(image.size) < 400 or image.format != "PNG":
                    raise ValueError(f"Invalid verified image for {aid}")
            raw = source.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            relative = Path("avito-cards/ready-price/verified-20260923") / f"{aid}-{digest[:12]}.png"
            target = PUBLIC / relative
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Existing image changed: {target}")
            operations.append((target, raw))
            images = ad.find("Images")
            if images is None:
                raise ValueError(f"No Images element for {aid}")
            for child in list(images):
                images.remove(child)
            etree.SubElement(images, "Image", url="https://splithome.ru/static/" + relative.as_posix())
            ad.remove(ad.find("DateEnd"))
            report.append({"ad_id": aid, "model": model, "image_sha256": digest,
                           "image": str(relative)})
        after = etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True)
        if not apply:
            return {"status": "dry_run", "ads": report, "feed_count": len(by_id)}
        backup = ROOT / "state/ready-price" / ("visual-repair-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        backup.mkdir()
        shutil.copy2(FEED, backup / "feed.xml")
        shutil.copy2(REGISTRY, backup / "visual-holds.json")
        for target, raw in operations:
            target.parent.mkdir(parents=True, exist_ok=True)
            pp._write_atomic_bytes(target, raw)
        candidate = backup / "candidate.xml"
        pp._write_atomic_bytes(candidate, after)
        pp.validate_feed(candidate, len(by_id), len(by_id))
        pp._write_atomic_bytes(FEED, after)
        doc["entries"] = {}
        pp._write_atomic_bytes(REGISTRY, json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))
        pp._write_atomic_bytes(backup / "repair-report.json",
                               json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        return {"status": "repaired_in_feed", "ads": report, "backup": str(backup),
                "feed_count": len(by_id)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(apply=args.apply), ensure_ascii=False, indent=2))
