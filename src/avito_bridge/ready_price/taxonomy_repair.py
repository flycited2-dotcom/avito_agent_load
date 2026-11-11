"""Journaled migration of obsolete taxonomy in the shared feed, under its lock."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid
from xml.etree import ElementTree as XML

from defusedxml import ElementTree as ET

from avito_bridge import profile_publish as pp
from avito_bridge.feed.taxonomy import correct_legacy_taxonomy


def repair_legacy_taxonomy(feed: Path, bridge: Path, public: Path) -> dict:
    """Caller holds profile-publish.lock; preserve IDs, content and ad count."""
    root = ET.fromstring(feed.read_bytes())
    if root.tag != "Ads":
        raise ValueError("Invalid Avito feed root")
    changes = []
    ids = set()
    for ad in root.findall("Ad"):
        aid = ad.findtext("Id")
        if not aid or aid in ids:
            raise ValueError("Missing or duplicate Avito ID")
        ids.add(aid)
        tags = {child.tag: child.text or "" for child in ad if len(child) == 0}
        revised = correct_legacy_taxonomy(tags)
        if tags == revised:
            continue
        fields = {}
        for tag in tags.keys() | revised.keys():
            if tags.get(tag) == revised.get(tag):
                continue
            nodes = ad.findall(tag)
            if len(nodes) > 1:
                raise ValueError(f"Duplicate taxonomy field {tag} in {aid}")
            fields[tag] = {"before": tags.get(tag), "after": revised.get(tag)}
            if tag not in revised:
                ad.remove(nodes[0])
            else:
                node = nodes[0] if nodes else XML.SubElement(ad, tag)
                node.text = revised[tag]
        changes.append({"ad_id": aid, "title": ad.findtext("Title"), "fields": fields})
    if not changes:
        return {"status": "no_changes", "changed": []}
    after = XML.tostring(root, encoding="utf-8", xml_declaration=True)
    candidate = bridge / "state/ready-price/taxonomy-candidate.xml"
    pp._write_atomic_bytes(candidate, after)
    pp.validate_feed(candidate, len(ids), len(ids))
    operations = [pp.LiveOperation(feed, after)]
    backup = bridge / "state/studio-backups" / (
        "taxonomy-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    common = Path(os.path.commonpath([str(bridge.resolve()), str(public.resolve())]))
    journal = pp._prepare_transaction(operations, backup, common,
                                      {"kind": "legacy_taxonomy", "changes": changes})
    try:
        pp._apply_operations(operations)
    except Exception:
        pp._restore_journal(backup, journal, bridge, public)
        pp._set_journal_status(backup, journal, "rolled_back")
        raise
    pp._set_journal_status(backup, journal, "committed")
    return {"status": "corrected_in_feed", "changed": changes, "backup": str(backup)}
