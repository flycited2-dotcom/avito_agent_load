from avito_bridge.state.store import StateStore


def test_upsert_and_changed(tmp_path):
    s = StateStore(tmp_path / "state.db")
    assert s.changed("abc", "hash1") is True       # новый
    s.record("abc", "hash1")
    assert s.changed("abc", "hash1") is False       # без изменений
    assert s.changed("abc", "hash2") is True        # изменился контент/цена


def test_persists_across_instances(tmp_path):
    db = tmp_path / "state.db"
    StateStore(db).record("x", "h")
    assert StateStore(db).changed("x", "h") is False
