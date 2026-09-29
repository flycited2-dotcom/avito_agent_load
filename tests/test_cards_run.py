from types import SimpleNamespace

from avito_bridge.cards_run import main, select_groups
from avito_bridge.catalog.series import SeriesGroup


def _group(key):
    return SeriesGroup(key=key, source="s", brand="b", series="x", category_id=2, members=[])


def test_select_groups_with_key_bypasses_whitelist():
    groups = [_group("a"), _group("b")]
    result = select_groups(groups, key="b", selected_series=frozenset({"a"}),
                           supplier_photo_series=frozenset())
    assert [g.key for g in result] == ["b"]


def test_select_groups_without_key_uses_strict_whitelist():
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


def test_cards_main_uses_profile_source_and_bridge_relative_state(
    monkeypatch, tmp_path, capsys
):
    cfg = SimpleNamespace(
        grouping="series",
        bridge_root=tmp_path,
        selected_series=frozenset(),
        cards=SimpleNamespace(
            supplier_photo_series=frozenset(),
            dir=str(tmp_path / "cards"),
        ),
        catalog=SimpleNamespace(manual_card_brief={}),
    )
    captured = {}
    monkeypatch.setattr("avito_bridge.cards_run.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "avito_bridge.cards_run.fetch_profile_offers", lambda loaded: ["offer"]
    )
    monkeypatch.setattr("avito_bridge.cards_run.group_by_series", lambda offers: [])
    monkeypatch.setattr(
        "avito_bridge.cards_run.config",
        lambda name, *args, **kwargs: (
            args[0]
            if args
            else {
                "FOTOGEN_API_URL": "https://agent.test",
                "FOTOGEN_API_TOKEN": "token",
                "FOTOGEN_QUEUE_DB": str(tmp_path / "queue.db"),
                "FOTOGEN_OUTPUT_DIR": str(tmp_path / "out"),
            }[name]
        ),
    )
    monkeypatch.setattr(
        "avito_bridge.cards_run.CardJobStore",
        lambda path: captured.setdefault("state_path", path),
    )

    def fake_run(groups, settings, store, manual_brief):
        captured["groups"] = groups
        return 0, 0

    monkeypatch.setattr("avito_bridge.cards_run.run_once", fake_run)

    assert main(["--config", "profiles/test.yaml"]) == 0
    assert captured["state_path"] == tmp_path / "state" / "card_jobs.db"
    assert "series=0" in capsys.readouterr().out


def test_cards_main_supports_per_item_profiles(monkeypatch, tmp_path, capsys):
    cfg = SimpleNamespace(
        grouping="per_item",
        bridge_root=tmp_path,
        selected_series=frozenset(),
        cards=SimpleNamespace(
            supplier_photo_series=frozenset(),
            dir=str(tmp_path / "cards"),
        ),
        catalog=SimpleNamespace(manual_card_brief={}),
    )
    groups = [_group("price_xls|item|pricexls:A-1")]
    monkeypatch.setattr("avito_bridge.cards_run.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "avito_bridge.cards_run.fetch_profile_offers", lambda loaded: ["offer"]
    )
    monkeypatch.setattr("avito_bridge.cards_run.group_per_item", lambda offers: groups)
    monkeypatch.setattr(
        "avito_bridge.cards_run.group_by_series",
        lambda offers: (_ for _ in ()).throw(AssertionError("series grouping must not run")),
    )
    monkeypatch.setattr(
        "avito_bridge.cards_run.config",
        lambda name, *args, **kwargs: (
            args[0]
            if args
            else {
                "FOTOGEN_API_URL": "https://agent.test",
                "FOTOGEN_API_TOKEN": "token",
                "FOTOGEN_QUEUE_DB": str(tmp_path / "queue.db"),
                "FOTOGEN_OUTPUT_DIR": str(tmp_path / "out"),
            }[name]
        ),
    )
    monkeypatch.setattr("avito_bridge.cards_run.CardJobStore", lambda path: object())
    monkeypatch.setattr(
        "avito_bridge.cards_run.run_once", lambda *args, **kwargs: (0, 0)
    )

    assert main(["--config", "profiles/per-item.yaml"]) == 0
    assert "series=1" in capsys.readouterr().out


def test_cards_main_all_bypasses_only_generation_whitelist(monkeypatch, tmp_path, capsys):
    cfg = SimpleNamespace(
        grouping="per_item",
        bridge_root=tmp_path,
        selected_series=frozenset({"selected-only"}),
        cards=SimpleNamespace(
            supplier_photo_series=frozenset(),
            dir=str(tmp_path / "cards"),
        ),
        catalog=SimpleNamespace(manual_card_brief={}),
    )
    groups = [_group("a"), _group("b")]
    captured = {}
    monkeypatch.setattr("avito_bridge.cards_run.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "avito_bridge.cards_run.fetch_profile_offers", lambda loaded: ["offer"]
    )
    monkeypatch.setattr("avito_bridge.cards_run.group_per_item", lambda offers: groups)
    monkeypatch.setattr(
        "avito_bridge.cards_run.config",
        lambda name, *args, **kwargs: (
            args[0]
            if args
            else {
                "FOTOGEN_API_URL": "https://agent.test",
                "FOTOGEN_API_TOKEN": "token",
                "FOTOGEN_QUEUE_DB": str(tmp_path / "queue.db"),
                "FOTOGEN_OUTPUT_DIR": str(tmp_path / "out"),
            }[name]
        ),
    )
    monkeypatch.setattr("avito_bridge.cards_run.CardJobStore", lambda path: object())

    def fake_run(passed_groups, *args, **kwargs):
        captured["groups"] = passed_groups
        return 0, 0

    monkeypatch.setattr("avito_bridge.cards_run.run_once", fake_run)

    assert main(["--config", "profiles/per-item.yaml", "--all"]) == 0
    assert captured["groups"] == groups
    assert "series=2" in capsys.readouterr().out
