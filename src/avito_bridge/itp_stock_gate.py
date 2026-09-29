"""Scoped live-feed stock gate for I-T-P-backed Avito advertisements.

Only explicitly mapped Avito IDs are touched.  Active items keep their exact
existing XML; zero-stock items are removed and later restored from a durable
template.  Unrelated advertisements are byte-semantically unchanged.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil

from lxml import etree
import yaml

from avito_bridge.feed.writer import write_atomic
from avito_bridge.ingest.itp_snapshot import load_itp_snapshot
from avito_bridge.avito.manual_stop import load_suppressed_ids


@dataclass(frozen=True)
class GateResult:
    before: int
    after: int
    removed: tuple[str, ...]
    restored: tuple[str, ...]
    repriced: tuple[str, ...]
    active_skus: tuple[int, ...]


def load_stock_mapping(path: str | Path) -> dict[int, str]:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    raw = payload.get("sku_to_ad_id")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("itp_stock_gate: sku_to_ad_id must be a non-empty object")
    result: dict[int, str] = {}
    seen_ids: set[str] = set()
    for sku_value, ad_value in raw.items():
        try:
            sku = int(sku_value)
        except (TypeError, ValueError):
            raise ValueError(f"itp_stock_gate: invalid SKU {sku_value!r}") from None
        ad_id = str(ad_value or "").strip()
        if sku <= 0 or not ad_id:
            raise ValueError(f"itp_stock_gate: invalid mapping {sku_value!r}: {ad_value!r}")
        if ad_id in seen_ids:
            raise ValueError(f"itp_stock_gate: duplicate Avito ID {ad_id}")
        result[sku] = ad_id
        seen_ids.add(ad_id)
    return result


def _read_ads(path: Path) -> etree._Element:
    try:
        root = etree.parse(str(path), etree.XMLParser(resolve_entities=False)).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ValueError(f"itp_stock_gate: invalid XML {path}: {exc}") from exc
    if root.tag != "Ads":
        raise ValueError(f"itp_stock_gate: expected Ads root in {path}")
    ids = [(ad.findtext("Id") or "").strip() for ad in root.findall("Ad")]
    if any(not ad_id for ad_id in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"itp_stock_gate: empty or duplicate advertisement ID in {path}")
    return root


def _new_root_like(root: etree._Element) -> etree._Element:
    return etree.Element(root.tag, **dict(root.attrib))


def _set_ad_price(ad: etree._Element, price: int) -> bool:
    price_node = ad.find("Price")
    ad_id = (ad.findtext("Id") or "").strip()
    if price_node is None:
        raise ValueError(f"itp_stock_gate: managed ad {ad_id} has no Price element")
    previous = (price_node.text or "").strip()
    price_node.text = str(price)
    return previous != str(price)


def apply_stock_gate(
    *,
    feed_path: str | Path,
    snapshot_path: str | Path,
    mapping_path: str | Path,
    template_path: str | Path,
    apply: bool = False,
    backup_dir: str | Path | None = None,
    max_age_seconds: int = 1_800,
    manual_stop_path: str | Path | None = None,
) -> GateResult:
    feed = Path(feed_path)
    templates_file = Path(template_path)
    mapping = load_stock_mapping(mapping_path)
    snapshot = load_itp_snapshot(snapshot_path, max_age_seconds=max_age_seconds)
    items_by_sku = {int(item["sku"]): item for item in snapshot["items"]}
    active_skus = {
        int(item["sku"])
        for item in snapshot["items"]
        if int(item.get("stock") or 0) > 0
    }
    active_ids = {ad_id for sku, ad_id in mapping.items() if sku in active_skus}
    if manual_stop_path is not None:
        active_ids -= load_suppressed_ids(Path(manual_stop_path))
    managed_ids = set(mapping.values())
    price_by_ad_id: dict[str, int] = {}
    for sku in sorted(set(mapping) & active_skus):
        raw_price = items_by_sku[sku].get("avitoPrice")
        try:
            price = int(raw_price)
        except (TypeError, ValueError):
            raise ValueError(f"itp_stock_gate: missing avitoPrice for active SKU {sku}") from None
        if price <= 0 or float(raw_price) != price:
            raise ValueError(f"itp_stock_gate: invalid avitoPrice for active SKU {sku}: {raw_price!r}")
        price_by_ad_id[mapping[sku]] = price

    current_root = _read_ads(feed)
    template_root = (
        _read_ads(templates_file)
        if templates_file.is_file()
        else _new_root_like(current_root)
    )
    templates = {
        (ad.findtext("Id") or "").strip(): deepcopy(ad)
        for ad in template_root.findall("Ad")
        if (ad.findtext("Id") or "").strip() in managed_ids
    }
    for ad in current_root.findall("Ad"):
        ad_id = (ad.findtext("Id") or "").strip()
        if ad_id in managed_ids:
            templates.setdefault(ad_id, deepcopy(ad))
    missing_templates = sorted(managed_ids - set(templates))
    if missing_templates:
        raise ValueError(
            "itp_stock_gate: no durable/current XML template for managed IDs: "
            + ", ".join(missing_templates)
        )

    result_root = _new_root_like(current_root)
    current_ids: set[str] = set()
    removed: list[str] = []
    repriced: list[str] = []
    for ad in current_root.findall("Ad"):
        ad_id = (ad.findtext("Id") or "").strip()
        current_ids.add(ad_id)
        if ad_id in managed_ids and ad_id not in active_ids:
            removed.append(ad_id)
            continue
        output_ad = deepcopy(ad)
        if ad_id in active_ids and _set_ad_price(output_ad, price_by_ad_id[ad_id]):
            repriced.append(ad_id)
        result_root.append(output_ad)

    restored: list[str] = []
    for ad_id in sorted(active_ids - current_ids):
        restored_ad = deepcopy(templates[ad_id])
        if _set_ad_price(restored_ad, price_by_ad_id[ad_id]):
            repriced.append(ad_id)
        result_root.append(restored_ad)
        restored.append(ad_id)

    before = len(current_root.findall("Ad"))
    after = len(result_root.findall("Ad"))
    if abs(after - before) > len(mapping):
        raise ValueError("itp_stock_gate: count delta exceeds the managed mapping")

    if apply:
        destination = Path(backup_dir or feed.parent / "itp-stock-backups")
        destination.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(feed, destination / f"avito-feed-before-{timestamp}.xml")
        template_document = etree.tostring(
            _templates_document(current_root, templates, managed_ids),
            encoding="unicode",
            pretty_print=True,
        )
        templates_file.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + template_document,
            templates_file,
        )
        result_document = etree.tostring(result_root, encoding="unicode", pretty_print=True)
        write_atomic(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + result_document,
            feed,
        )

    return GateResult(
        before=before,
        after=after,
        removed=tuple(sorted(removed)),
        restored=tuple(restored),
        repriced=tuple(sorted(repriced)),
        active_skus=tuple(sorted(set(mapping) & active_skus)),
    )


def _templates_document(
    current_root: etree._Element,
    templates: dict[str, etree._Element],
    managed_ids: set[str],
) -> etree._Element:
    root = _new_root_like(current_root)
    for ad_id in sorted(managed_ids):
        root.append(deepcopy(templates[ad_id]))
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate mapped Avito ads by I-T-P Crimea stock")
    parser.add_argument("--feed", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--templates", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=1_800)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manual-stop", type=Path, default=Path("state/manual-stop-main.json"))
    args = parser.parse_args(argv)
    result = apply_stock_gate(
        feed_path=args.feed,
        snapshot_path=args.snapshot,
        mapping_path=args.mapping,
        template_path=args.templates,
        backup_dir=args.backup_dir,
        max_age_seconds=args.max_age_seconds,
        apply=args.apply,
        manual_stop_path=args.manual_stop,
    )
    print(
        f"itp-stock-gate apply={args.apply} before={result.before} after={result.after} "
        f"active={len(result.active_skus)} removed={len(result.removed)} "
        f"restored={len(result.restored)} repriced={len(result.repriced)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
