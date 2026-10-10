from pathlib import Path

import pytest
from lxml import etree

from avito_bridge.feed.merge import merge_feeds


def _feed(path: Path, *ids: str) -> Path:
    entries = "".join(
        f"<Ad><Id>{ad_id}</Id><Title>Товар</Title></Ad>" for ad_id in ids
    )
    path.write_text(f'<?xml version="1.0"?><Ads>{entries}</Ads>', encoding="utf-8")
    return path


def test_merge_feeds_keeps_all_ads_and_writes_atomically(tmp_path: Path):
    output = tmp_path / "merged.xml"
    count = merge_feeds(
        [_feed(tmp_path / "conditioners.xml", "ac-1"), _feed(tmp_path / "appliances.xml", "kbt-1")],
        output,
    )

    assert count == 2
    root = etree.parse(str(output)).getroot()
    assert root.tag == "Ads"
    assert [node.text for node in root.xpath("/Ads/Ad/Id")] == ["ac-1", "kbt-1"]


def test_merge_feeds_rejects_duplicate_ad_ids(tmp_path: Path):
    with pytest.raises(ValueError, match="Duplicate Avito Id"):
        merge_feeds(
            [_feed(tmp_path / "one.xml", "same"), _feed(tmp_path / "two.xml", "same")],
            tmp_path / "merged.xml",
        )
