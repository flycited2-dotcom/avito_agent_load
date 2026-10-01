"""Restore stock-verified archived ads under the original Avito IDs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

from lxml import etree

from avito_bridge import profile_publish as pp

RENEWABLE_REASONS = {"expired_avito_listing", "archive_hold"}


def restore_verified_ads(feed: Path, stops: Path, verified_ads: dict,
                         bridge: Path, public: Path, *, apply=False) -> dict:
    """Caller supplies exact ads checked against fresh supplier snapshots.

    Explicit owner exclusions are never released. XML and released exclusions
    commit together under the caller's shared profile publication lock.
    """
    root = etree.parse(str(feed), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    stop_doc = json.loads(stops.read_text(encoding="utf-8"))
    entries = stop_doc["entries"]
    existing = {ad.findtext("Id"): ad for ad in root.findall("Ad")}
    renewed, preserved_owner_stops = [], []
    for aid, template in sorted(verified_ads.items()):
        stop = entries.get(aid)
        if not stop:
            continue
        if stop.get("reason") not in RENEWABLE_REASONS:
            preserved_owner_stops.append(aid)
            continue
        ad = deepcopy(template)
        if ad.tag != "Ad" or ad.findtext("Id") != aid:
            raise ValueError("Renewal template identity mismatch")
        for node in list(ad.findall("DateEnd")):
            ad.remove(node)
        if aid in existing:
            root.remove(existing[aid])
        root.append(ad)
        del entries[aid]
        renewed.append(aid)
    result = {"status": "candidate", "before": len(existing),
              "after": len(root.findall("Ad")), "renewed": renewed,
              "preserved_owner_stops": preserved_owner_stops,
              "held_archive": sorted(aid for aid, row in entries.items()
                                     if row.get("reason") in RENEWABLE_REASONS)}
    if not renewed:
        return {**result, "status": "no_changes"}
    payload = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    candidate = bridge / "state/renewal-candidate.xml"
    pp._write_atomic_bytes(candidate, payload)
    pp.validate_feed(candidate, result["after"], result["before"])
    if not apply:
        return result
    backup = bridge / "state/studio-backups" / ("stock-renewal-" + str(int(time.time())) + "-" + uuid.uuid4().hex[:8])
    operations = [pp.LiveOperation(feed, payload), pp.LiveOperation(
        stops, (json.dumps(stop_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))]
    common = Path(os.path.commonpath([str(bridge), str(public)]))
    journal = pp._prepare_transaction(operations, backup, common, {"profile": "stock-verified-renewal", "ad_ids": renewed})
    try:
        pp._apply_operations(operations)
        pp.validate_feed(feed, result["after"], result["before"])
    except Exception:
        pp._restore_journal(backup, journal, bridge, public)
        pp._set_journal_status(backup, journal, "rolled_back")
        raise
    pp._set_journal_status(backup, journal, "committed")
    return {**result, "status": "committed", "backup": str(backup)}
