import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from defusedxml import ElementTree as ET
import xml.etree.ElementTree as XML

from avito_bridge.ready_price.identity import seed_published_manifest
from avito_bridge.ready_price.remote import apply_updates
from tests.test_ready_price import ingest, policy, release
from tests.test_ready_price_identity import manifest_for


def feed(path: Path):
    root = XML.Element("Ads", formatVersion="3", target="Avito.ru")
    for aid, price in [("keep-historical-id", "4193"), ("unrelated", "100")]:
        ad = XML.SubElement(root, "Ad")
        for tag, value in [("Id", aid), ("Title", aid), ("Description", "description"), ("Price", price)]:
            XML.SubElement(ad, tag).text = value
        images = XML.SubElement(ad, "Images")
        XML.SubElement(images, "Image", url="https://example.test/image.jpg")
    path.parent.mkdir(parents=True)
    path.write_bytes(XML.tostring(root, encoding="utf-8", xml_declaration=True))


def prepared(tmp_path, policy):
    database = tmp_path / "bridge/state/ready-price/catalog.sqlite"
    source = release(tmp_path / "release")
    ingest(source, database, policy)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE releases SET generated_at=? WHERE status='accepted'",
                   (datetime.now(timezone.utc).isoformat(),))
    seed_published_manifest(database, manifest_for(source, database))
    live = tmp_path / "public/avito-feed.xml"
    feed(live)
    stops = tmp_path / "bridge/state/manual-stop-main.json"
    stops.parent.mkdir(parents=True, exist_ok=True)
    stops.write_text('{"version":1,"entries":{}}')
    return database, live, stops


def test_managed_price_update_is_atomic_and_idempotent(tmp_path, policy):
    database, live, stops = prepared(tmp_path, policy)
    db = sqlite3.connect(database)
    row = db.execute("SELECT article,data FROM catalog ORDER BY article LIMIT 1").fetchone()
    data = json.loads(row[1]); data["avito_price"] = 4200
    db.execute("UPDATE catalog SET data=? WHERE article=?", (json.dumps(data), row[0]))
    db.commit(); db.close()
    result = apply_updates(database, live, stops, tmp_path / "bridge", tmp_path / "public")
    assert result["status"] == "committed" and result["updated"] == ["keep-historical-id"]
    root = ET.parse(live).getroot()
    assert {a.findtext("Id"): a.findtext("Price") for a in root.findall("Ad")} == {
        "keep-historical-id": "4200", "unrelated": "100"}
    assert apply_updates(database, live, stops, tmp_path / "bridge", tmp_path / "public")["status"] == "no_changes"


def test_manual_stop_removes_only_bound_ad(tmp_path, policy):
    database, live, stops = prepared(tmp_path, policy)
    stops.write_text('{"version":1,"entries":{"keep-historical-id":{"reason":"owner_stop"}}}')
    result = apply_updates(database, live, stops, tmp_path / "bridge", tmp_path / "public")
    assert result["removed"] == ["keep-historical-id"]
    assert [a.findtext("Id") for a in ET.parse(live).getroot().findall("Ad")] == ["unrelated"]
