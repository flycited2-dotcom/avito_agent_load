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


def test_revision_changes_id_but_zero_keeps_legacy():
    # revision — «перевыпуск» объявления, отклонённого модерацией: новый Id = новая проверка.
    # revision=0 обязан давать СТАРЫЙ id (все живые объявления не должны перевыпуститься).
    legacy = make_ad_id("ritualb2b:venok-dafna", "simferopol")
    assert make_ad_id("ritualb2b:venok-dafna", "simferopol", revision=0) == legacy
    v2 = make_ad_id("ritualb2b:venok-dafna", "simferopol", revision=2)
    assert v2 != legacy
    assert make_ad_id("ritualb2b:venok-dafna", "simferopol", revision=3) != v2
