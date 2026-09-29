"""Серверный обработчик готового прайса: импорт и безопасное обновление привязанных ID."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
import time
import uuid

from defusedxml import ElementTree as ET
import xml.etree.ElementTree as XML

from avito_bridge import profile_publish as pp
from .identity import identity_db, seed_published_manifest
from .store import SOURCE, import_release


def safe_extract(bundle: Path, target: Path) -> None:
    if bundle.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("ready-price bundle too large")
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 5:
            raise ValueError("unexpected ready-price bundle layout")
        for member in members:
            name = Path(member.name)
            if member.name not in {"release.json", "price.xlsx", "telegram.json"}:
                raise ValueError("unexpected ready-price bundle member")
            if name.is_absolute() or ".." in name.parts or not member.isfile():
                raise ValueError("unsafe ready-price bundle")
        archive.extractall(target, filter="data")


def _latest_release(db):
    row = db.execute("SELECT * FROM releases WHERE source=? AND status='accepted' ORDER BY generated_at DESC LIMIT 1", (SOURCE,)).fetchone()
    if not row:
        raise ValueError("no accepted ready-price release")
    generated = datetime.fromisoformat(row["generated_at"])
    if generated.tzinfo is None or datetime.now(timezone.utc) - generated.astimezone(timezone.utc) > timedelta(days=4):
        raise ValueError("latest ready-price release is stale")
    return row


def apply_updates(database: Path, feed: Path, manual_stops: Path, bridge: Path, public: Path,
                  *, max_updates=50, max_removals=10):
    db = identity_db(database)
    try:
        latest = _latest_release(db)
        bindings = db.execute("""SELECT b.*, c.data, c.present, c.missing_since
            FROM source_bindings b JOIN catalog c ON c.source=b.source AND c.article=b.article
            WHERE b.source=?""", (SOURCE,)).fetchall()
        accepted = [r[0] for r in db.execute("SELECT sha256 FROM releases WHERE source=? AND status='accepted' ORDER BY generated_at DESC LIMIT 2", (SOURCE,))]
        present_pairs = {(r[0], r[1]) for r in db.execute(
            "SELECT sha256,article FROM snapshot_items WHERE sha256 IN (%s)" % ",".join("?" for _ in accepted), accepted
        )} if accepted else set()
    finally:
        db.close()
    stops = json.loads(manual_stops.read_text(encoding="utf-8"))["entries"]
    before = feed.read_bytes()
    root = ET.fromstring(before)
    ads = root.findall("Ad")
    by_id = {a.findtext("Id"): a for a in ads}
    updated, removed, skipped = [], [], []
    for row in bindings:
        aid = row["ad_id"]
        ad = by_id.get(aid)
        if ad is None:
            skipped.append({"ad_id": aid, "reason": "not_in_live_feed"})
            continue
        if aid in stops:
            root.remove(ad)
            removed.append(aid)
            continue
        item = json.loads(row["data"])
        if row["present"]:
            price = str(item["avito_price"])
            node = ad.find("Price")
            if node is None:
                raise ValueError(f"managed ad has no Price: {aid}")
            if node.text != price:
                node.text = price
                updated.append(aid)
            continue
        # Снятие только после двух разных полных принятых снимков без артикула и 24 часов.
        if len(accepted) < 2 or any((sha, row["article"]) in present_pairs for sha in accepted):
            skipped.append({"ad_id": aid, "reason": "absence_not_confirmed_twice"})
            continue
        missing = datetime.fromisoformat(row["missing_since"])
        if datetime.now(timezone.utc) - missing.astimezone(timezone.utc) < timedelta(hours=24):
            skipped.append({"ad_id": aid, "reason": "absence_grace_period"})
            continue
        root.remove(ad)
        removed.append(aid)
    if len(updated) > max_updates or len(removed) > max_removals:
        raise ValueError("ready-price safety limit exceeded")
    result = {"latest_sha256": latest["sha256"], "before": len(ads),
              "after": len(root.findall("Ad")), "updated": updated, "removed": removed,
              "skipped": skipped, "status": "no_changes"}
    if not updated and not removed:
        return result
    after = XML.tostring(root, encoding="utf-8", xml_declaration=True)
    stage = bridge / "state/ready-price"
    stage.mkdir(parents=True, exist_ok=True)
    candidate = stage / "candidate.xml"
    pp._write_atomic_bytes(candidate, after)
    pp.validate_feed(candidate, result["after"], max(1, result["before"] - max_removals))
    operation = pp.LiveOperation(feed, after)
    backup = bridge / "state/studio-backups" / ("ready-price-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    common = Path(__import__("os").path.commonpath([str(bridge), str(public)]))
    journal = pp._prepare_transaction([operation], backup, common,
                                      {"profile": SOURCE, "public_feed": str(feed), "source_sha256": latest["sha256"]})
    try:
        pp._apply_operations([operation])
        pp.validate_feed(feed, result["after"], max(1, result["before"] - max_removals))
    except Exception:
        pp._restore_journal(backup, journal, bridge, public)
        pp._set_journal_status(backup, journal, "rolled_back")
        raise
    pp._set_journal_status(backup, journal, "committed")
    result.update(status="committed", backup=str(backup))
    return result


def run(inbox: Path, archive: Path, database: Path, categories: Path, published_manifest: Path,
        feed: Path, manual_stops: Path, bridge: Path, public: Path):
    inbox.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    processed = []
    with pp._exclusive_lock(bridge / "state/profile-publish.lock"):
        bundles = []
        for bundle in inbox.glob("*.tgz"):
            try:
                with tarfile.open(bundle, "r:gz") as packed:
                    member = packed.getmember("release.json")
                    release = json.load(packed.extractfile(member))
                bundles.append((release.get("generated_at", ""), bundle))
            except Exception:
                bundles.append(("", bundle))
        for _, bundle in sorted(bundles):
            if not re.fullmatch(r"[0-9a-f]{64}\.tgz", bundle.name):
                continue
            digest = bundle.stem
            try:
                with tempfile.TemporaryDirectory(dir=bridge / "state") as temp:
                    release_dir = Path(temp)
                    safe_extract(bundle, release_dir)
                    result = import_release(release_dir / "release.json", database, categories)
                    if result["sha256"] != digest:
                        raise ValueError("bundle filename checksum mismatch")
                    seed_published_manifest(database, published_manifest)
                    destination = archive / bundle.name
                    bundle.replace(destination)
                    pp._write_atomic_bytes(destination.with_suffix(".json"), json.dumps(result, ensure_ascii=False, indent=2).encode())
                    processed.append(result)
            except Exception as exc:
                processed.append({"sha256": digest, "status": "error", "reason": type(exc).__name__})
        update = apply_updates(database, feed, manual_stops, bridge, public)
    return {"processed": processed, "update": update}
