from avito_bridge.feed.writer import write_atomic


def test_write_atomic_creates_file(tmp_path):
    target = tmp_path / "feed.xml"
    write_atomic("<Ads/>", target)
    assert target.read_text(encoding="utf-8") == "<Ads/>"


def test_write_atomic_overwrites_without_partial(tmp_path):
    target = tmp_path / "feed.xml"
    write_atomic("<old/>", target)
    write_atomic("<new/>", target)
    assert target.read_text(encoding="utf-8") == "<new/>"
    assert not list(tmp_path.glob("*.tmp"))   # временный файл убран
