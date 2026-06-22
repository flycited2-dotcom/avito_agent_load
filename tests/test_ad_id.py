from avito_bridge.feed.ad_id import make_ad_id


def test_stable_across_calls():
    assert make_ad_id("rusklimat:NC7", "simferopol") == make_ad_id("rusklimat:NC7", "simferopol")


def test_distinct_per_city():
    assert make_ad_id("rusklimat:NC7", "simferopol") != make_ad_id("rusklimat:NC7", "sevastopol")


def test_distinct_per_sku():
    assert make_ad_id("a:1", "simferopol") != make_ad_id("a:2", "simferopol")


def test_format_is_alphanumeric_and_bounded():
    v = make_ad_id("rusklimat:NC7", "simferopol")
    assert v.isalnum() and 8 <= len(v) <= 40
