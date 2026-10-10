import json
from datetime import datetime, timezone

from lxml import etree
import pytest

from avito_bridge.itp_stock_gate import apply_stock_gate


def write_feed(path, ids):
    root = etree.Element("Ads", formatVersion="3", target="Avito.ru")
    for ad_id in ids:
        ad = etree.SubElement(root, "Ad")
        etree.SubElement(ad, "Id").text = ad_id
        etree.SubElement(ad, "Title").text = f"Title {ad_id}"
        etree.SubElement(ad, "Price").text = "99900"
    path.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True))


def write_snapshot(path, active_skus):
    path.write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "items": [{"sku": sku, "stock": 1, "avitoPrice": sku * 100} for sku in active_skus],
    }), encoding="utf-8")


def write_mapping(path):
    path.write_text(
        'sku_to_ad_id:\n  "100": managed-a\n  "200": managed-b\n',
        encoding="utf-8",
    )


def test_manual_stop_prevents_stock_restore(tmp_path):
    feed, snapshot, mapping, templates, stop = [tmp_path / n for n in ('f.xml', 's.json', 'm.yaml', 't.xml', 'stop.json')]
    write_feed(feed, ['unmanaged', 'managed-a'])
    write_feed(templates, ['managed-a', 'managed-b'])
    write_snapshot(snapshot, [100, 200])
    write_mapping(mapping)
    stop.write_text(json.dumps({'entries': {'managed-b': {}}}))
    result = apply_stock_gate(feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
                              template_path=templates, manual_stop_path=stop, apply=True)
    assert not result.restored
    assert result.after == 2


def test_gate_removes_only_zero_stock_managed_ad_and_writes_backup(tmp_path):
    feed = tmp_path / "feed.xml"
    snapshot = tmp_path / "snapshot.json"
    mapping = tmp_path / "mapping.yaml"
    templates = tmp_path / "templates.xml"
    backups = tmp_path / "backups"
    write_feed(feed, ["unmanaged", "managed-a", "managed-b"])
    write_snapshot(snapshot, [100])
    write_mapping(mapping)

    result = apply_stock_gate(
        feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
        template_path=templates, backup_dir=backups, apply=True,
    )

    assert result.before == 3
    assert result.after == 2
    assert result.removed == ("managed-b",)
    assert result.repriced == ("managed-a",)
    assert [ad.findtext("Id") for ad in etree.parse(str(feed)).getroot()] == [
        "unmanaged", "managed-a",
    ]
    assert etree.parse(str(feed)).getroot().findall("Ad")[-1].findtext("Price") == "10000"
    assert sorted(ad.findtext("Id") for ad in etree.parse(str(templates)).getroot()) == [
        "managed-a", "managed-b",
    ]
    assert len(list(backups.glob("*.xml"))) == 1


def test_gate_restores_exact_template_when_stock_returns(tmp_path):
    feed = tmp_path / "feed.xml"
    snapshot = tmp_path / "snapshot.json"
    mapping = tmp_path / "mapping.yaml"
    templates = tmp_path / "templates.xml"
    write_feed(feed, ["unmanaged", "managed-a", "managed-b"])
    write_snapshot(snapshot, [100])
    write_mapping(mapping)
    apply_stock_gate(
        feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
        template_path=templates, apply=True,
    )
    write_snapshot(snapshot, [100, 200])

    result = apply_stock_gate(
        feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
        template_path=templates, apply=True,
    )

    assert result.restored == ("managed-b",)
    restored = etree.parse(str(feed)).getroot().findall("Ad")[-1]
    assert restored.findtext("Title") == "Title managed-b"
    assert restored.findtext("Price") == "20000"


def test_dry_run_does_not_mutate_feed_or_create_templates(tmp_path):
    feed = tmp_path / "feed.xml"
    snapshot = tmp_path / "snapshot.json"
    mapping = tmp_path / "mapping.yaml"
    templates = tmp_path / "templates.xml"
    write_feed(feed, ["managed-a", "managed-b"])
    original = feed.read_bytes()
    write_snapshot(snapshot, [100])
    write_mapping(mapping)

    result = apply_stock_gate(
        feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
        template_path=templates, apply=False,
    )

    assert result.removed == ("managed-b",)
    assert feed.read_bytes() == original
    assert not templates.exists()


def test_gate_requires_price_for_every_active_managed_sku(tmp_path):
    feed = tmp_path / "feed.xml"
    snapshot = tmp_path / "snapshot.json"
    mapping = tmp_path / "mapping.yaml"
    templates = tmp_path / "templates.xml"
    write_feed(feed, ["managed-a", "managed-b"])
    write_snapshot(snapshot, [100])
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["items"][0].pop("avitoPrice")
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    write_mapping(mapping)

    with pytest.raises(ValueError, match="missing avitoPrice for active SKU 100"):
        apply_stock_gate(
            feed_path=feed, snapshot_path=snapshot, mapping_path=mapping,
            template_path=templates, apply=False,
        )
