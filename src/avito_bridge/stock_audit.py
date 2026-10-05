"""Check every live advertisement against explicit supplier bindings.

Unavailable sources defer their bound ads. Missing bindings never count as stock.
Holds persist before the feed changes, so another builder cannot republish them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from lxml import etree as ET

from avito_bridge import profile_publish as pp
from avito_bridge.avito.manual_stop import _read_json, _write_json_atomic
from avito_bridge.config import load_config
from avito_bridge.catalog.series import group_by_series, group_per_item
from avito_bridge.feed.ad_id import make_ad_id
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.ingest.btopt_availability import read_availability
from avito_bridge.ingest.itp_snapshot import load_itp_snapshot
from avito_bridge.ready_price.store import source_freshness_problem
from avito_bridge.pricing.pricing import compute_price


HOLD_REASON = "stock_source_unverified"


def supplier_state(bridge: Path, bindings: dict) -> tuple[dict, dict]:
    states, errors = {}, {}
    profiles = {b["profile"] for b in bindings.values() if b.get("kind") == "profile"}
    for name in profiles:
        try:
            cfg = load_config(bridge / name)
            offers = fetch_profile_offers(cfg)
            if not offers:
                raise ValueError("empty_supplier_response")
            states["profile:" + name] = {o.supplier_sku: o for o in offers
                                       if o.source != "manual" and not o.forced}
            states["pricing:" + name] = cfg.pricing
            groups = group_per_item(offers) if cfg.grouping == "per_item" else group_by_series(offers)
            for group in groups:
                skus = [o.supplier_sku for o in group.members if o.source != "manual" and not o.forced]
                if skus:
                    anchor = cfg.feed.ad_id_anchor.get(group.key, group.representative.supplier_sku)
                    aid = make_ad_id(anchor, "simferopol")
                    if aid not in bindings or bindings[aid].get("kind") == "profile":
                        bindings[aid] = {"kind": "profile", "profile": name, "supplier_skus": skus}
        except Exception as exc:
            # Never expose a DSN or credentials embedded in exception messages.
            errors["profile:" + name] = type(exc).__name__
    try:
        with sqlite3.connect(bridge / "state/ready-price/catalog.sqlite") as db:
            db.row_factory = sqlite3.Row
            release = db.execute("SELECT * FROM releases WHERE source='telegram-nikita' "
                                 "AND status='accepted' ORDER BY generated_at DESC LIMIT 1").fetchone()
            problem = source_freshness_problem(release)
            if problem:
                raise ValueError(problem)
            states["ready"] = {r["article"]: json.loads(r["data"]) for r in db.execute(
                "SELECT article,data FROM catalog WHERE source='telegram-nikita' AND present=1")}
            for r in db.execute("SELECT ad_id,article FROM source_bindings WHERE source='telegram-nikita'"):
                bindings.setdefault(r["ad_id"], {"kind": "ready", "article": r["article"]})
    except Exception as exc:
        errors["ready"] = type(exc).__name__
    try:
        snapshot = load_itp_snapshot(bridge / "runtime/itp-crimea/latest.json")
        states["itp"] = {str(item["sku"]): item for item in snapshot["items"]}
    except Exception as exc:
        errors["itp"] = type(exc).__name__
    try:
        states["btopt"] = read_availability(Path(
            "/opt/content-factory/state/prices/mail__info_simfer_com_ru_прайс_лист_опт_xlsx.xlsx"))["items"]
    except Exception as exc:
        errors["btopt"] = type(exc).__name__
    return states, errors


def check_binding(binding: dict, states: dict) -> tuple[str, dict]:
    kind = binding.get("kind")
    key = "profile:" + binding["profile"] if kind == "profile" else kind
    if key not in states:
        return ("unverified" if not kind else "source_unavailable"), {}
    source = states[key]
    if kind == "profile":
        available = [source[sku] for sku in binding["supplier_skus"]
                     if sku in source and source[sku].stock > 0]
        detail = {
            "supplier_skus": [o.supplier_sku for o in available],
            "quantity": sum(o.stock for o in available)}
        pricing = states.get("pricing:" + binding["profile"])
        if pricing:
            prices = [compute_price(o, pricing) for o in available]
            valid = [p.price for p in prices if p.ok and p.price]
            if valid:
                detail["price"] = min(valid)
        return ("verified" if available else "not_available"), detail
    item = source.get(str(binding.get("sku") if kind == "itp" else binding.get("article")), {})
    if kind == "itp":
        verified = int(item.get("stock") or 0) > 0
        detail = {"quantity": item.get("stock", 0), "price": item.get("avitoPrice")}
    elif kind == "btopt":
        verified = item.get("main_available") is True
        detail = {"supplier_name": item.get("name"), "main_available": item.get("main_available")}
    else:
        verified = item.get("availability") == "supplier_price_present"
        detail = {"supplier_name": item.get("name"), "evidence": "present_in_fresh_supplier_price",
                  "price": item.get("avito_price")}
    return ("verified" if verified else "not_available"), detail


def audit(bridge: Path, feed: Path, registry: Path, report: Path, *, apply=False, states=None, errors=None):
    bindings = _read_json(registry, {"bindings": {}})["bindings"]
    if not isinstance(bindings, dict) or not bindings or any(not isinstance(b, dict) for b in bindings.values()):
        raise ValueError("Missing or invalid supplier bindings: preserve the current feed")
    if states is None:
        states, errors = supplier_state(bridge, bindings)
    stop_path = bridge / "state/manual-stop-main.json"
    stops = _read_json(stop_path, {"version": 1, "entries": {}})
    templates_path = report.with_name("held-templates.json")
    templates = _read_json(templates_path, {})
    before = feed.read_bytes()
    root = ET.fromstring(before, ET.XMLParser(resolve_entities=False, no_network=True))
    if root.tag != "Ads":
        raise ValueError("Invalid feed root")
    ids = [a.findtext("Id") for a in root]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Invalid or duplicate advertisement IDs")
    stamp = datetime.now(timezone.utc).isoformat()
    rows, removed, restored = [], [], []
    for ad in list(root):
        aid = ad.findtext("Id")
        binding = bindings.get(aid, {})
        status, detail = check_binding(binding, states)
        rows.append({"ad_id": aid, "title": ad.findtext("Title"), "status": status,
                     "binding": binding, "evidence": detail})
        if status in {"unverified", "not_available"}:
            templates.setdefault(aid, {"xml": ET.tostring(ad, encoding="unicode"), "held_at": stamp})
            stops["entries"].setdefault(aid, {"ad_id": aid, "title": ad.findtext("Title"),
                "reason": HOLD_REASON, "stock_status": status, "created_at": stamp})
            root.remove(ad)
            removed.append(aid)
    for aid in list(templates):
        if aid not in ids:
            status, detail = check_binding(bindings.get(aid, {}), states)
            own_hold = stops["entries"].get(aid, {}).get("reason") == HOLD_REASON
            price = detail.get("price")
            if own_hold and status == "verified" and type(price) is int and price > 0:
                ad = ET.fromstring(templates[aid]["xml"], ET.XMLParser(resolve_entities=False, no_network=True))
                if ad.tag != "Ad" or ad.findtext("Id") != aid or ad.find("Price") is None:
                    raise ValueError("Invalid stock restoration template")
                # A dated listing needs an expiry decision separate from stock.
                if ad.findtext("DateEnd") is None:
                    ad.find("Price").text = str(price)
                    root.append(ad)
                    del stops["entries"][aid]
                    del templates[aid]
                    restored.append(aid)
                    rows.append({"ad_id": aid, "title": ad.findtext("Title"), "status": "verified",
                                 "binding": bindings[aid], "evidence": detail})
                    continue
            rows.append({"ad_id": aid, "title": stops["entries"].get(aid, {}).get("title"),
                         "status": "held", "supplier_status": status,
                         "binding": bindings.get(aid, {}), "evidence": detail})
    result = {"generated_at": stamp, "before": len(ids), "after": len(root),
              "verified": sum(r["status"] == "verified" for r in rows),
              "held": sum(r["status"] == "held" for r in rows) + len(removed),
              "deferred": sum(r["status"] == "source_unavailable" for r in rows),
              "removed": removed, "restored": restored, "source_errors": errors or {}, "rows": rows}
    if apply:
        if removed or restored:
            backup = report.parent / ("feed-before-" + stamp.replace(":", "-") + ".xml")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(feed, backup)
            if removed:
                # Persist exclusions before dropping ads. On restoration, write
                # the valid feed before clearing its durable exclusion.
                _write_json_atomic(stop_path, {**stops, "updated_at": stamp})
                _write_json_atomic(templates_path, templates)
            pp._write_atomic_bytes(feed, ET.tostring(root, encoding="utf-8", xml_declaration=True))
            _write_json_atomic(stop_path, {**stops, "updated_at": stamp})
            _write_json_atomic(templates_path, templates)
        _write_json_atomic(registry, {"bindings": bindings})
        _write_json_atomic(report, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", type=Path, default=Path("/opt/avito-bridge"))
    parser.add_argument("--feed", type=Path, default=Path("/opt/oasis/staticfiles/avito-feed.xml"))
    args = parser.parse_args()
    state = args.bridge / "state/stock-audit"
    with pp._exclusive_lock(args.bridge / "state/profile-publish.lock"):
        result = audit(args.bridge, args.feed, state / "bindings.json", state / "last-run.json", apply=True)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
