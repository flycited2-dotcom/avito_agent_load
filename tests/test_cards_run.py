from avito_bridge.cards_run import select_groups
from avito_bridge.catalog.series import SeriesGroup


def _group(key):
    return SeriesGroup(key=key, source="s", brand="b", series="x", category_id=2, members=[])


def test_select_groups_with_key_bypasses_whitelist():
    groups = [_group("a"), _group("b")]
    result = select_groups(groups, key="b", selected_series=frozenset({"a"}),
                           supplier_photo_series=frozenset())
    assert [g.key for g in result] == ["b"]


def test_select_groups_without_key_uses_whitelist_plus_forced():
    groups = [_group("a"), _group("b")]
    result = select_groups(groups, key=None, selected_series=frozenset({"a"}),
                           supplier_photo_series=frozenset())
    assert [g.key for g in result] == ["a"]


def test_select_groups_without_key_and_without_whitelist_keeps_all():
    groups = [_group("a"), _group("b")]
    result = select_groups(groups, key=None, selected_series=frozenset(),
                           supplier_photo_series=frozenset())
    assert [g.key for g in result] == ["a", "b"]


def test_select_groups_excludes_supplier_photo_series_even_with_explicit_key():
    groups = [_group("a")]
    result = select_groups(groups, key="a", selected_series=frozenset(),
                           supplier_photo_series=frozenset({"a"}))
    assert result == []
