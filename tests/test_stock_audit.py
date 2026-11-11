import json
from types import SimpleNamespace

from avito_bridge.stock_audit import audit, check_binding


def setup(tmp_path, bindings):
    state = tmp_path / "state"
    state.mkdir()
    registry = state / "bindings.json"
    registry.write_text(json.dumps({"bindings": bindings}))
    feed = tmp_path / "feed.xml"
    feed.write_text('<Ads><Ad><Id>A</Id><Title>Known</Title><Price>100</Price></Ad>'
                    '<Ad><Id>B</Id><Title>Unknown</Title><Price>200</Price></Ad></Ads>')
    return feed, registry, state / "report.json"


def test_unverified_listing_is_held_durably_and_owner_stops_are_preserved(tmp_path):
    feed, registry, report = setup(tmp_path, {"A": {"kind": "itp", "sku": "123"}})
    stop = tmp_path / "state/manual-stop-main.json"
    stop.write_text(json.dumps({"version": 1, "entries": {"owner": {"reason": "manual_avito_removal"}}}))
    result = audit(tmp_path, feed, registry, report, apply=True,
                   states={"itp": {"123": {"stock": 2}}})
    assert result["verified"] == 1 and result["removed"] == ["B"]
    assert b'<Id>B</Id>' not in feed.read_bytes()
    entries = json.loads(stop.read_text())["entries"]
    assert entries["owner"]["reason"] == "manual_avito_removal"
    assert entries["B"]["reason"] == "stock_source_unverified"
    assert '<Price>200</Price>' in json.loads(report.with_name('held-templates.json').read_text())["B"]["xml"]
    result = audit(tmp_path, feed, registry, report, apply=True,
                   states={"itp": {"123": {"stock": 2}}})
    assert result["removed"] == [] and result["held"] == 1


def test_supplier_failure_defers_known_stock_and_does_not_mass_remove(tmp_path):
    feed, registry, report = setup(tmp_path, {"A": {"kind": "itp", "sku": "123"},
                                            "B": {"kind": "ready", "article": "B-1"}})
    original = feed.read_bytes()
    result = audit(tmp_path, feed, registry, report, apply=True, states={}, errors={"itp": "TimeoutError"})
    assert result["deferred"] == 2 and result["removed"] == []
    assert feed.read_bytes() == original


def test_exact_supplier_identity_and_positive_stock_required():
    assert check_binding({"kind": "itp", "sku": "123"}, {"itp": {"124": {"stock": 2}}})[0] == "not_available"
    assert check_binding({"kind": "btopt", "article": "x"},
                         {"btopt": {"x": {"main_available": False, "management_available": True}}})[0] == "not_available"
    state = {"profile:p": {"sku1": SimpleNamespace(stock=0),
                           "sku2": SimpleNamespace(stock=3, supplier_sku="sku2")}}
    assert check_binding({"kind": "profile", "profile": "p", "supplier_skus": ["sku1", "sku2"]}, state)[0] == "verified"


def test_returned_supplier_stock_restores_same_id_with_current_price(tmp_path):
    feed, registry, report = setup(tmp_path, {"A": {"kind": "itp", "sku": "123"},
                                            "B": {"kind": "ready", "article": "B-1"}})
    audit(tmp_path, feed, registry, report, apply=True, states={"itp": {"123": {"stock": 1}}, "ready": {}})
    result = audit(tmp_path, feed, registry, report, apply=True,
                   states={"itp": {"123": {"stock": 1}}, "ready": {
                       "B-1": {"availability": "supplier_price_present", "avito_price": 350}}})
    assert result["restored"] == ["B"]
    assert b'<Id>B</Id><Title>Unknown</Title><Price>350</Price>' in feed.read_bytes()
    assert "B" not in json.loads((tmp_path / 'state/manual-stop-main.json').read_text())["entries"]


def test_missing_registry_preserves_feed(tmp_path):
    import pytest
    feed, registry, report = setup(tmp_path, {})
    before = feed.read_bytes()
    with pytest.raises(ValueError, match='supplier bindings'):
        audit(tmp_path, feed, registry, report, apply=True, states={})
    assert feed.read_bytes() == before
