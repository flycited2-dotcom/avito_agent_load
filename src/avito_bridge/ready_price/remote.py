"""Серверный обработчик готового прайса: импорт и безопасное обновление привязанных ID."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
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
from .store import SOURCE, import_release, source_freshness_problem


def safe_extract(bundle: Path, target: Path) -> None:
    if bundle.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("ready-price bundle too large")
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 5:
            raise ValueError("unexpected ready-price bundle layout")
        for member in members:
            name = Path(member.name)
            if member.name not in {"release.json", "price.xlsx", "supplier.xls", "supplier.xlsx", "telegram.json"}:
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
        verified_supplier = json.loads(latest["manifest"]).get("schema_version") == 2
        if verified_supplier and source_freshness_problem(latest):
            return {"latest_sha256": latest["sha256"], "status": "blocked_supplier_snapshot",
                    "reason": source_freshness_problem(latest), "updated": [], "removed": [],
                    "restored": [], "skipped": []}
        latest_items = {r["article"]: json.loads(r["data"]) for r in db.execute(
            "SELECT article,data FROM snapshot_items WHERE sha256=?", (latest["sha256"],))}
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
    state_file = bridge / "state/ready-price/stock-removed.json"
    templates = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    if not isinstance(templates, dict):
        raise ValueError("invalid stock removal registry")
    updated, removed, restored, skipped = [], [], [], []
    templates_before = dict(templates)
    for row in bindings:
        aid = row["ad_id"]
        ad = by_id.get(aid)
        stock_verified = verified_supplier and latest_items.get(row["article"], {}).get("availability") == "supplier_price_present"
        if ad is None:
            if aid in templates and stock_verified and aid not in stops:
                if len(restored) >= max_removals:
                    skipped.append({"ad_id": aid, "reason": "restore_batch_limit"})
                    continue
                recovered = ET.fromstring(base64.b64decode(templates[aid]["xml"], validate=True))
                if recovered.tag != "Ad" or recovered.findtext("Id") != aid or templates[aid]["article"] != row["article"]:
                    raise ValueError("invalid stock restoration template")
                recovered.find("Price").text = str(latest_items[row["article"]]["avito_price"])
                root.append(recovered)
                restored.append(aid)
                del templates[aid]
                continue
            skipped.append({"ad_id": aid, "reason": "not_in_live_feed"})
            continue
        if stock_verified:
            templates.pop(aid, None)
        if aid in stops:
            if len(removed) >= max_removals:
                skipped.append({"ad_id": aid, "reason": "removal_batch_limit"})
                continue
            root.remove(ad)
            removed.append(aid)
            templates.pop(aid, None)
            continue
        item = json.loads(row["data"])
        if verified_supplier and not stock_verified:
            if len(removed) >= max_removals:
                skipped.append({"ad_id": aid, "reason": "removal_batch_limit"})
                continue
            templates[aid] = {"article": row["article"], "xml": base64.b64encode(XML.tostring(ad, encoding="utf-8")).decode("ascii")}
            root.remove(ad)
            removed.append(aid)
            continue
        if row["present"]:
            price = str(item["avito_price"])
            node = ad.find("Price")
            if node is None:
                raise ValueError(f"managed ad has no Price: {aid}")
            if node.text != price:
                if len(updated) >= max_updates:
                    skipped.append({"ad_id": aid, "reason": "price_batch_limit"})
                else:
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
        if len(removed) >= max_removals:
            skipped.append({"ad_id": aid, "reason": "removal_batch_limit"})
            continue
        root.remove(ad)
        removed.append(aid)
    if len(updated) > max_updates or len(removed) > max_removals or len(restored) > max_removals:
        raise ValueError("ready-price safety limit exceeded")
    result = {"latest_sha256": latest["sha256"], "before": len(ads),
              "after": len(root.findall("Ad")), "updated": updated, "removed": removed,
              "restored": restored, "skipped": skipped, "status": "no_changes"}
    if not updated and not removed and not restored and templates == templates_before:
        return result
    after = XML.tostring(root, encoding="utf-8", xml_declaration=True)
    stage = bridge / "state/ready-price"
    stage.mkdir(parents=True, exist_ok=True)
    candidate = stage / "candidate.xml"
    pp._write_atomic_bytes(candidate, after)
    pp.validate_feed(candidate, result["after"], max(1, result["before"] - max_removals))
    operations = [pp.LiveOperation(feed, after), pp.LiveOperation(
        state_file, (json.dumps(templates, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))]
    backup = bridge / "state/studio-backups" / ("ready-price-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    common = Path(__import__("os").path.commonpath([str(bridge), str(public)]))
    journal = pp._prepare_transaction(operations, backup, common,
                                      {"profile": SOURCE, "public_feed": str(feed), "source_sha256": latest["sha256"]})
    try:
        pp._apply_operations(operations)
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


def stock_report(database: Path, feed: Path, manual_stops: Path, bridge: Path) -> dict:
    """Текущие привязанные карточки и случаи для решения владельца."""
    db = identity_db(database)
    try:
        latest = _latest_release(db)
        current = {r["article"]: json.loads(r["data"]) for r in db.execute(
            "SELECT article,data FROM snapshot_items WHERE sha256=?", (latest["sha256"],))}
        bindings = db.execute("SELECT article,ad_id,source_name FROM source_bindings WHERE source=? ORDER BY article", (SOURCE,)).fetchall()
    finally:
        db.close()
    feed_ids = {ad.findtext("Id") for ad in ET.parse(feed).getroot().findall("Ad")}
    stops = json.loads(manual_stops.read_text(encoding="utf-8"))["entries"]
    template_path = bridge / "state/ready-price/stock-removed.json"
    templates = json.loads(template_path.read_text(encoding="utf-8")) if template_path.is_file() else {}
    rows = []
    for binding in bindings:
        aid, article = binding["ad_id"], binding["article"]
        supplier_present = current.get(article, {}).get("availability") == "supplier_price_present"
        if aid in stops:
            reason = stops[aid].get("reason")
            if reason == "expired_avito_listing":
                state, action = "expired_review", "review_renew_or_regenerate" if supplier_present else "review_cancel"
            elif reason == "archive_hold":
                state, action = "archived_review", "review_expiry_or_owner_removal"
            else:
                state, action = "manual_stop", "keep_stopped"
        elif aid in templates:
            state, action = "removed_from_feed", "restore_automatically_if_article_returns"
        elif aid in feed_ids:
            state, action = "in_feed", "none" if supplier_present else "check_source"
        else:
            state, action = "missing_from_feed", "review_regenerate_or_cancel" if supplier_present else "review_cancel"
        rows.append({"article": article, "ad_id": aid, "name": binding["source_name"],
                     "supplier_present": supplier_present, "state": state, "action": action})
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "source_sha256": latest["sha256"],
            "rows": rows}


def expiration_report(database: Path, manual_stops: Path) -> dict:
    """Expiry needs its own review and must not be described as an owner removal."""
    db = identity_db(database)
    try:
        release = db.execute("SELECT * FROM releases WHERE source=? AND status='accepted' "
                             "ORDER BY generated_at DESC LIMIT 1", (SOURCE,)).fetchone()
        fresh = source_freshness_problem(release) is None
        bindings = {r['ad_id']: dict(r) for r in db.execute(
            "SELECT b.ad_id,b.article,c.present,c.data FROM source_bindings b JOIN catalog c "
            "ON c.source=b.source AND c.article=b.article WHERE b.source=?", (SOURCE,))}
    finally:
        db.close()
    stops = json.loads(manual_stops.read_text(encoding='utf-8'))['entries']
    rows = []
    for aid, stop in sorted(stops.items()):
        reason = stop.get('reason')
        if reason not in {'expired_avito_listing', 'archive_hold'}:
            continue
        binding = bindings.get(aid)
        supplier_present = None if not binding or not fresh else bool(binding['present'] and
            json.loads(binding['data']).get('availability') == 'supplier_price_present')
        action = ('review_renew_or_regenerate' if supplier_present else 'review_cancel'
                  if supplier_present is False else 'confirm_source_before_renew')
        rows.append({'ad_id': aid, 'avito_id': stop.get('avito_id'), 'title': stop.get('title'),
                     'article': binding['article'] if binding else None,
                     'state': 'expired' if reason == 'expired_avito_listing' else 'archive_reason_unconfirmed',
                     'supplier_present': supplier_present, 'action': action})
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'rows': rows}
