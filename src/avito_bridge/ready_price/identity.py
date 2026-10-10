"""Закрепление источника и проверяемые кандидаты совпадений.

Нечёткий поиск здесь может только задержать товар. Он не переносит владельца
цены, не связывает модель автоматически и не выдаёт разрешение публикации.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from defusedxml import ElementTree as ET

from .catalog import clean
from .store import SOURCE, connect, export_catalog


class IdentityConflict(ValueError):
    pass


def normalized(value):
    return clean(value).casefold()


def compact(value):
    return re.sub(r"[^a-zа-яё0-9]", "", normalized(value))


def proposed_id(article):
    """Стабильный ID, не зависящий от номера строки/файла или текущей цены."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,70}", article):
        return "nikita-" + article
    return "nikita-" + hashlib.sha256((SOURCE + ":" + article).encode()).hexdigest()[:24]


def identity_db(database):
    db = connect(database)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS ad_ownership (
            ad_id TEXT PRIMARY KEY, price_owner TEXT NOT NULL, availability_owner TEXT NOT NULL,
            evidence TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_bindings (
            source TEXT NOT NULL, article TEXT NOT NULL, ad_id TEXT NOT NULL REFERENCES ad_ownership(ad_id),
            source_name TEXT NOT NULL, source_brand TEXT NOT NULL, model TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            evidence TEXT NOT NULL, PRIMARY KEY(source,article), UNIQUE(source,ad_id)
        );
    """)
    return db


def seed_published_manifest(database: Path, manifest_path: Path):
    """Переиспользовать ID первой партии только после сверки исходной строки."""
    data = manifest_path.read_bytes()
    manifest = json.loads(data)
    if manifest.get("source") != SOURCE or manifest.get("schema_version") != 1:
        raise IdentityConflict("unexpected_manifest_source")
    evidence = json.dumps({"kind": "existing_published_batch", "manifest_sha256": hashlib.sha256(data).hexdigest(),
                           "batch": manifest["batch"]})
    db = identity_db(database)
    try:
        db.execute("BEGIN IMMEDIATE")
        release = db.execute("SELECT status FROM releases WHERE sha256=?", (manifest["source_sha256"],)).fetchone()
        if not release or release["status"] != "accepted":
            raise IdentityConflict("published_source_snapshot_not_imported")
        for product in manifest["products"]:
            row = db.execute("SELECT data FROM snapshot_items WHERE sha256=? AND article=?",
                             (manifest["source_sha256"], product["article"])).fetchone()
            if not row:
                raise IdentityConflict("published_article_missing")
            item = json.loads(row["data"])
            if item["name"] != product["source_name"] or item["row"] != product["source_row"]:
                raise IdentityConflict("published_source_row_mismatch")
            if item["avito_price"] != product["avito_price"]:
                raise IdentityConflict("published_source_price_mismatch")
            if not compact(product["model"]) or compact(product["model"]) not in compact(item["name"]):
                raise IdentityConflict("published_model_mismatch")
            aid = product["ad_id"]
            existing = db.execute("SELECT * FROM ad_ownership WHERE ad_id=?", (aid,)).fetchone()
            if existing and (existing["price_owner"] != SOURCE or existing["availability_owner"] != SOURCE):
                raise IdentityConflict("another_source_already_owns_ad:" + aid)
            binding = db.execute("SELECT * FROM source_bindings WHERE source=? AND article=?", (SOURCE, item["article"])).fetchone()
            if binding and (binding["ad_id"] != aid or binding["source_name"] != item["name"] or binding["source_brand"] != item["brand"] or binding["model"] != product["model"]):
                raise IdentityConflict("binding_already_exists_with_different_identity")
            db.execute("INSERT OR IGNORE INTO ad_ownership VALUES (?,?,?,?)", (aid, SOURCE, SOURCE, evidence))
            db.execute("INSERT OR IGNORE INTO source_bindings VALUES (?,?,?,?,?,?,?,?)",
                       (SOURCE, item["article"], aid, item["name"], item["brand"], product["model"], manifest["source_sha256"], evidence))
        db.commit()
        return {"bound": len(manifest["products"]), "marketplace_status": "requires_fresh_report"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read_feed(path):
    root = ET.parse(path).getroot()
    if root.tag != "Ads":
        raise IdentityConflict("invalid_feed_root")
    ads = [{"ad_id": a.findtext("Id"), "title": a.findtext("Title", ""),
            "model": a.findtext("Model", ""), "price": a.findtext("Price", "")}
           for a in root.findall("Ad")]
    if any(not a["ad_id"] for a in ads) or len({a["ad_id"] for a in ads}) != len(ads):
        raise IdentityConflict("duplicate_or_empty_feed_id")
    return ads


def candidate_matches(item, ads):
    model = compact(item.get("model_hint", ""))
    if len(model) < 4 or not re.search(r"[a-zа-яё]", model) or not re.search(r"\d", model):
        return []
    return [ad for ad in ads if model in compact(ad["title"] + " " + ad.get("model", ""))]


def reconcile(database: Path, feed: Path, manual_stops: Path, itp_owned_ids=()):
    """Read-only сверка с кэшем: без полного свежего аккаунта новые ID не утверждаются."""
    ads = read_feed(feed)
    stops = json.loads(manual_stops.read_text(encoding="utf-8"))
    if stops.get("version") != 1 or not isinstance(stops.get("entries"), dict):
        raise IdentityConflict("invalid_manual_stop_snapshot")
    stop_entries = stops["entries"]
    stopped_ads = [{"ad_id": aid, "title": entry.get("title", ""), "model": ""} for aid, entry in stop_entries.items()]
    items = export_catalog(database)
    names = Counter((normalized(x["brand"]), normalized(x["name"])) for x in items if x["present"])
    db = identity_db(database)
    try:
        bindings = {r["article"]: dict(r) for r in db.execute("SELECT * FROM source_bindings WHERE source=?", (SOURCE,))}
        owners = {r["ad_id"]: dict(r) for r in db.execute("SELECT * FROM ad_ownership")}
    finally:
        db.close()
    itp_owned_ids = set(itp_owned_ids)
    results = []
    for item in items:
        row = {**item, "ad_id": None, "proposed_id": proposed_id(item["article"]), "matched_ads": [],
               "price_owner": None, "availability_owner": None,
               "marketplace_status": "unconfirmed", "publication_allowed": False}
        binding = bindings.get(item["article"])
        if binding:
            aid = binding["ad_id"]
            owner = owners[aid]
            row.update(ad_id=aid, price_owner=owner["price_owner"], availability_owner=owner["availability_owner"])
            if owner["price_owner"] != SOURCE or owner["availability_owner"] != SOURCE:
                row.update(status="source_ownership_conflict", reason="binding_owner_is_another_source")
            elif aid in itp_owned_ids:
                row.update(status="source_ownership_conflict", reason="id_also_reserved_by_itp")
            elif aid in stop_entries:
                row.update(status="manual_stop", reason=stop_entries[aid].get("reason", "manual_stop"))
            elif normalized(item["name"]) != normalized(binding["source_name"]) or normalized(item["brand"]) != normalized(binding["source_brand"]):
                row.update(status="identity_changed", reason="article_reused_or_model_variant_changed")
            elif not item["present"]:
                pass
            elif names[(normalized(item["brand"]), normalized(item["name"]))] > 1:
                row.update(status="duplicate_source_identity", reason="same_name_multiple_articles")
            else:
                row.update(status="matched_existing", reason="existing_id_preserved_requires_current_avito_and_stop_report",
                           identity_verified=True, model=binding["model"])
        elif item["bucket"] != "excluded" and item["present"]:
            matches = candidate_matches(item, ads)
            stop_matches = candidate_matches(item, stopped_ads)
            row["matched_ads"] = [{**ad, "price_owner": "itp-crimea" if ad["ad_id"] in itp_owned_ids else
                                    owners.get(ad["ad_id"], {}).get("price_owner", "existing_source_unresolved"),
                                   "match_method": "model_hint_candidate_only"} for ad in matches]
            if names[(normalized(item["brand"]), normalized(item["name"]))] > 1:
                row.update(status="duplicate_source_identity", reason="same_name_multiple_articles")
            elif stop_matches:
                row.update(status="awaiting_manual_stop_match", reason="possible_manually_stopped_model", stop_matches=stop_matches)
            elif matches:
                row.update(status="awaiting_existing_ad_match", reason="verify_exact_model_variant_colour_and_price_owner")
            elif item["bucket"] in {"small", "large", "tv"}:
                row.update(status="awaiting_account_reconciliation", reason="full_fresh_account_catalog_unavailable")
        results.append(row)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "reference_is_cached": True,
            "reference_feed_sha256": hashlib.sha256(feed.read_bytes()).hexdigest(),
            "manual_stop_snapshot_updated_at": stops.get("updated_at"),
            "publication_enabled": False, "counts": dict(Counter(x["status"] for x in results)), "items": results}
