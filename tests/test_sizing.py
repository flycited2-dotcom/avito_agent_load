from avito_bridge.content.sizing import (
    size_from_btu, size_from_kw, nearest_standard, parse_kw, derive_size,
)


def test_plain_ksize():
    assert size_from_btu(7, category_id=2) == 7
    assert size_from_btu(12, category_id=2) == 12


def test_no_area_map_by_default():
    # карта площадей ОТКЛЮЧЕНА (была источником ошибок 30→9); btu_calc уже стандартизован на ingest
    assert size_from_btu(30, category_id=2) == 30
    assert size_from_btu(25, category_id=2) == 25


def test_full_btu_divided():
    assert size_from_btu(9000, category_id=2) == 9


def test_garbage_none():
    assert size_from_btu(0, category_id=2) is None
    assert size_from_btu(None, category_id=2) is None


def test_size_from_kw_standard_buckets():
    assert size_from_kw(2.05) == 7
    assert size_from_kw(2.40) == 7      # Royal Clima FC22
    assert size_from_kw(2.85) == 9      # FUNAI SG25
    assert size_from_kw(3.20) == 12     # Hisense AS-12
    assert size_from_kw(5.00) == 18     # Shiratama DJ50 / Elysium ELB-18
    assert size_from_kw(7.50) == 24     # FUNAI SG75
    assert size_from_kw(8.79) == 30     # Elysium Inverter ELB-I30
    assert size_from_kw(0) is None and size_from_kw(None) is None


def test_nearest_standard():
    assert nearest_standard(10) == 9
    assert nearest_standard(13) == 12
    assert nearest_standard(16) == 18
    assert nearest_standard(26) == 24


def test_parse_kw():
    assert parse_kw("5.00 (1.90 - 5.20)") == 5.0
    assert parse_kw("3.20 ( - )") == 3.2
    assert parse_kw("2,4") == 2.4
    assert parse_kw(None) is None


def test_derive_size_prefers_kw():
    assert derive_size(3.20, 10, 2) == 12       # кВт побеждает кривой btu_calc=10
    assert derive_size(None, 16, 2) == 18       # нет кВт → btu к ближайшему стандарту
    assert derive_size(None, None, 2) is None
