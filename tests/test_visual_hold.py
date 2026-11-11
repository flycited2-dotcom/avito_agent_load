import json
from xml.etree import ElementTree as XML

import pytest

from avito_bridge.ready_price.visual_hold import apply_visual_holds


def test_visual_hold_only_expires_exact_reviewed_listing(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text("<Ads><Ad><Id>nikita-a</Id><Title>Чайник Gorenje K17DWD</Title>"
                    "<Price>2000</Price></Ad><Ad><Id>nikita-b</Id>"
                    "<Title>Другой товар</Title></Ad></Ads>", encoding="utf-8")
    registry = tmp_path / "holds.json"
    registry.write_text(json.dumps({"version": 1, "entries": {
        "nikita-a": {"model": "K17DWD", "date_end": "2026-09-22T00:00:00+03:00"},
    }}), encoding="utf-8")
    result = apply_visual_holds(feed, registry)
    assert result == {"configured": 1, "present": 1, "changed": ["nikita-a"]}
    rows = XML.parse(feed).getroot().findall("Ad")
    assert rows[0].findtext("DateEnd") == "2026-09-22T00:00:00+03:00"
    assert rows[1].find("DateEnd") is None
    assert apply_visual_holds(feed, registry)["changed"] == []


def test_visual_hold_aborts_if_ad_id_now_points_to_other_product(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text("<Ads><Ad><Id>nikita-a</Id><Title>Кофемолка Korting</Title></Ad></Ads>", encoding="utf-8")
    registry = tmp_path / "holds.json"
    registry.write_text(json.dumps({"version": 1, "entries": {
        "nikita-a": {"model": "K17DWD", "date_end": "2026-09-22T00:00:00+03:00"},
    }}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        apply_visual_holds(feed, registry)
    assert "DateEnd" not in feed.read_text(encoding="utf-8")
