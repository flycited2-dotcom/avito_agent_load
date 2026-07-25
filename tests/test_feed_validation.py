import pytest

from avito_bridge.feed.validation import existing_ad_count, validate_count_drop


def test_existing_ad_count_reads_only_valid_ads_feed(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text("<Ads><Ad/><Ad/></Ads>", encoding="utf-8")
    assert existing_ad_count(feed) == 2
    feed.write_text("<broken", encoding="utf-8")
    assert existing_ad_count(feed) is None


def test_validate_count_drop_blocks_unexpected_inventory_loss():
    validate_count_drop(70, previous_count=100, max_drop_fraction=0.30)
    with pytest.raises(ValueError, match="было 100, стало 69"):
        validate_count_drop(69, previous_count=100, max_drop_fraction=0.30)


@pytest.mark.parametrize("fraction", [-0.1, 1.1])
def test_validate_count_drop_rejects_invalid_fraction(fraction):
    with pytest.raises(ValueError, match="between 0 and 1"):
        validate_count_drop(1, previous_count=1, max_drop_fraction=fraction)
