import json
import sqlite3

from lxml import etree
import pytest

from avito_bridge.ready_price.identity import (IdentityConflict, identity_db, proposed_id,
                                               reconcile, seed_published_manifest)
from avito_bridge.ready_price.store import export_catalog
from tests.test_ready_price import NOW, ingest, policy, release


def manifest_for(path, db):
    source = json.loads(path.read_text())
    item = export_catalog(db)[0]
    manifest = path.parent / "published.json"
    manifest.write_text(json.dumps({"schema_version": 1, "source": "telegram-nikita", "batch": "test",
        "source_sha256": source["source_sha256"], "products": [{"article": item["article"],
        "ad_id": "keep-historical-id", "source_name": item["name"], "source_row": item["row"],
        "avito_price": item["avito_price"], "model": "CF-MC0"}]}))
    return manifest


def references(tmp_path, *, stopped=False):
    root = etree.Element("Ads")
    for aid, title in [("keep-historical-id", "Мультиварка Comfee CF-MC0 белая"),
                       ("itp-historical-id", "Мультиварка Comfee CF-MC1 черная")]:
        ad = etree.SubElement(root, "Ad")
        etree.SubElement(ad, "Id").text = aid
        etree.SubElement(ad, "Title").text = title
    feed = tmp_path / "feed.xml"
    feed.write_bytes(etree.tostring(root))
    stops = tmp_path / "stops.json"
    stops.write_text(json.dumps({"version": 1, "entries": {"keep-historical-id": {"reason": "owner_stop"}} if stopped else {}}))
    return feed, stops


def prepared(tmp_path, policy):
    db = tmp_path / "catalog.sqlite"
    path = release(tmp_path / "one")
    ingest(path, db, policy)
    manifest = manifest_for(path, db)
    seed_published_manifest(db, manifest)
    return db, manifest


def test_seed_preserves_historical_id_and_source_on_repeat(tmp_path, policy):
    db, manifest = prepared(tmp_path, policy)
    seed_published_manifest(db, manifest)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_bindings").fetchone()[0] == 1
        assert connection.execute("SELECT price_owner,availability_owner FROM ad_ownership").fetchone() == ("telegram-nikita", "telegram-nikita")
    result = reconcile(db, *references(tmp_path))
    first = result["items"][0]
    assert first["ad_id"] == "keep-historical-id" and first["status"] == "matched_existing"
    assert not first["publication_allowed"] and first["marketplace_status"] == "unconfirmed"


def test_manual_stop_wins_over_existing_mapping(tmp_path, policy):
    db, _ = prepared(tmp_path, policy)
    first = reconcile(db, *references(tmp_path, stopped=True))["items"][0]
    assert first["status"] == "manual_stop" and not first["publication_allowed"]


def test_other_source_ownership_cannot_be_stolen(tmp_path, policy):
    db, manifest = prepared(tmp_path, policy)
    connection = identity_db(db)
    connection.execute("UPDATE ad_ownership SET price_owner='itp-crimea'")
    connection.commit()
    connection.close()
    with pytest.raises(IdentityConflict, match="another_source"):
        seed_published_manifest(db, manifest)


def test_itp_match_with_different_colour_remains_candidate(tmp_path, policy):
    db, _ = prepared(tmp_path, policy)
    rows = reconcile(db, *references(tmp_path), itp_owned_ids=["itp-historical-id"])["items"]
    item = rows[1]
    assert item["ad_id"] is None and item["status"] == "awaiting_existing_ad_match"
    assert item["matched_ads"][0]["price_owner"] == "itp-crimea"
    assert item["matched_ads"][0]["match_method"] == "model_hint_candidate_only"


def test_same_article_with_changed_variant_requires_review(tmp_path, policy):
    db, _ = prepared(tmp_path, policy)
    connection = identity_db(db)
    row = connection.execute("SELECT article,data FROM catalog ORDER BY article LIMIT 1").fetchone()
    data = json.loads(row["data"])
    data["name"] = data["name"].replace("белая", "черная")
    connection.execute("UPDATE catalog SET data=? WHERE article=?", (json.dumps(data), row["article"]))
    connection.commit()
    connection.close()
    first = reconcile(db, *references(tmp_path))["items"][0]
    assert first["status"] == "identity_changed" and not first["publication_allowed"]


def test_unmatched_item_is_not_called_unique_without_fresh_account(tmp_path, policy):
    db, _ = prepared(tmp_path, policy)
    result = reconcile(db, *references(tmp_path))
    assert result["reference_is_cached"]
    assert result["items"][2]["status"] == "awaiting_account_reconciliation"
    assert all(not x["publication_allowed"] for x in result["items"])


def test_duplicate_source_names_are_held(tmp_path, policy):
    db, _ = prepared(tmp_path, policy)
    connection = identity_db(db)
    rows = connection.execute("SELECT article,data FROM catalog ORDER BY article LIMIT 2").fetchall()
    first, second = (json.loads(row["data"]) for row in rows)
    second["name"] = first["name"]
    connection.execute("UPDATE catalog SET data=? WHERE article=?", (json.dumps(second), rows[1]["article"]))
    connection.commit()
    connection.close()
    results = reconcile(db, *references(tmp_path))["items"]
    assert results[0]["status"] == results[1]["status"] == "duplicate_source_identity"


def test_bad_manifest_does_not_partially_bind_products(tmp_path, policy):
    db = tmp_path / "catalog.sqlite"
    path = release(tmp_path / "one")
    ingest(path, db, policy)
    manifest = manifest_for(path, db)
    data = json.loads(manifest.read_text())
    data["products"].append({**data["products"][0], "article": "missing"})
    manifest.write_text(json.dumps(data))
    with pytest.raises(IdentityConflict):
        seed_published_manifest(db, manifest)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ad_ownership").fetchone()[0] == 0


def test_proposed_id_is_stable_and_ascii_for_cyrillic_sku():
    assert proposed_id("00-00001507") == "nikita-00-00001507"
    assert proposed_id("Ту-00004008") == proposed_id("Ту-00004008")
    assert proposed_id("Ту-00004008").isascii()
    assert proposed_id("Ту-00004008") != proposed_id("Ту-00004009")
