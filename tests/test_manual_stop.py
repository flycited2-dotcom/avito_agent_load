import json
from avito_bridge.avito.manual_stop import expired_listing


def test_expired_listing_is_distinct_from_manual_removal():
    now = "2026-09-30T12:00:00+00:00"
    assert expired_listing({"section": {"slug": "stopped_by_expiration"}}, now)
    assert expired_listing({"avito_date_end": "2026-09-29T18:07:00+03:00"}, now)
    assert not expired_listing({"avito_date_end": "2026-10-30T18:07:00+03:00"}, now)
    assert not expired_listing({}, now)
from pathlib import Path

from lxml import etree

from avito_bridge.avito.manual_stop import (
    load_suppressed_ids,
    suppress_feed,
    sync_manual_stops,
)


def _write_feed(path: Path) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ads formatVersion="3" target="Avito.ru">'
        '<Ad><Id>a1</Id><Title>One</Title></Ad>'
        '<Ad><Id>a2</Id><Title>Two</Title></Ad>'
        '</Ads>',
        encoding="utf-8",
    )


class FakeClient:
    def __init__(self, statuses):
        self.statuses = statuses

    def last_successful_items(self, ad_ids=None):
        return [
            {"ad_id": "a1", "avito_id": 101, "avito_status": self.statuses["a1"]},
            {"ad_id": "a2", "avito_id": 102, "avito_status": self.statuses["a2"]},
        ]

    def list_items(self):
        return [
            {"id": 101, "status": self.statuses["a1"]},
            {"id": 102, "status": self.statuses["a2"]},
        ]


def test_active_to_removed_becomes_permanent_stop(tmp_path):
    feed = tmp_path / "feed.xml"
    stop = tmp_path / "stop.json"
    observations = tmp_path / "observations.json"
    _write_feed(feed)

    first = sync_manual_stops(
        FakeClient({"a1": "active", "a2": "blocked"}),
        feed, stop, observations,
    )
    assert first["added"] == 0

    second = sync_manual_stops(
        FakeClient({"a1": "removed", "a2": "rejected"}),
        feed, stop, observations,
    )
    assert second["added"] == 1
    assert load_suppressed_ids(stop) == {"a1"}

    sync_manual_stops(
        FakeClient({"a1": "active", "a2": "active"}),
        feed, stop, observations,
    )
    assert load_suppressed_ids(stop) == {"a1"}


def test_bootstrap_imports_existing_removed_but_not_old(tmp_path):
    feed = tmp_path / "feed.xml"
    stop = tmp_path / "stop.json"
    observations = tmp_path / "observations.json"
    _write_feed(feed)
    result = sync_manual_stops(
        FakeClient({"a1": "removed", "a2": "old"}),
        feed, stop, observations,
        bootstrap_removed=True,
    )
    assert result["added"] == 1
    assert load_suppressed_ids(stop) == {"a1"}


def test_bootstrap_after_observation_only_seed_imports_removed(tmp_path):
    feed = tmp_path / "feed.xml"
    stop = tmp_path / "stop.json"
    observations = tmp_path / "observations.json"
    _write_feed(feed)
    client = FakeClient({"a1": "removed", "a2": "active"})
    assert sync_manual_stops(client, feed, stop, observations)["added"] == 0
    assert sync_manual_stops(
        client, feed, stop, observations, bootstrap_removed=True
    )["added"] == 1
    assert load_suppressed_ids(stop) == {"a1"}


def test_suppress_feed_removes_only_stopped_ads(tmp_path):
    source = tmp_path / "feed.xml"
    target = tmp_path / "filtered.xml"
    stop = tmp_path / "stop.json"
    _write_feed(source)
    stop.write_text(json.dumps({"version": 1, "entries": {"a2": {}}}), "utf-8")

    removed, remaining = suppress_feed(source, target, stop)
    assert (removed, remaining) == (1, 1)
    root = etree.parse(str(target)).getroot()
    assert root.findtext("Ad/Id") == "a1"


def test_missing_live_status_never_uses_historical_upload_status(tmp_path):
    feed, stop, obs = (tmp_path / n for n in ('feed.xml', 'stop.json', 'obs.json'))
    _write_feed(feed)
    sync_manual_stops(FakeClient({'a1': 'active', 'a2': 'active'}), feed, stop, obs)
    client = FakeClient({'a1': 'removed', 'a2': 'removed'})
    client.list_items = lambda: []
    result = sync_manual_stops(client, feed, stop, obs)
    assert result['observed'] == 0
    assert not load_suppressed_ids(stop)


def test_cached_mapping_survives_missing_upload_item(tmp_path):
    feed, stop, obs = (tmp_path / n for n in ('feed.xml', 'stop.json', 'obs.json'))
    _write_feed(feed)
    sync_manual_stops(FakeClient({'a1': 'active', 'a2': 'active'}), feed, stop, obs)
    client = FakeClient({'a1': 'removed', 'a2': 'active'})
    client.last_successful_items = lambda **kwargs: []
    assert sync_manual_stops(client, feed, stop, obs)['added'] == 1


def test_unchanged_filter_preserves_exact_feed_bytes(tmp_path):
    feed, stop = tmp_path / 'feed.xml', tmp_path / 'stop.json'
    _write_feed(feed)
    before = feed.read_bytes()
    assert suppress_feed(feed, feed, stop) == (0, 2)
    assert feed.read_bytes() == before


def test_archive_hold_is_explicit_and_cannot_auto_reactivate(tmp_path):
    feed, stop, obs = (tmp_path / n for n in ('feed.xml', 'stop.json', 'obs.json'))
    _write_feed(feed)
    sync_manual_stops(FakeClient({'a1': 'active', 'a2': 'active'}), feed, stop, obs)
    sync_manual_stops(FakeClient({'a1': 'old', 'a2': 'blocked'}), feed, stop, obs, hold_archive=True)
    assert load_suppressed_ids(stop) == {'a1'}
    assert json.loads(stop.read_text())['entries']['a1']['reason'] == 'archive_hold'
    sync_manual_stops(FakeClient({'a1': 'active', 'a2': 'active'}), feed, stop, obs, hold_archive=True)
    assert load_suppressed_ids(stop) == {'a1'}
