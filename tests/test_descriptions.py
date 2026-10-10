import json
import pytest
from avito_bridge.content.descriptions import load_descriptions


def test_load_descriptions_maps_key_to_text(tmp_path):
    (tmp_path / "a.txt").write_text("Текст A", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"src|brand|series": "a.txt", "src|x|y": "missing.txt"}, ensure_ascii=False),
        encoding="utf-8")
    out = load_descriptions(tmp_path / "manifest.json")
    assert out == {"src|brand|series": "Текст A"}    # отсутствующий файл пропущен


def test_load_descriptions_missing_manifest():
    assert load_descriptions("/no/such/manifest.json") == {}


def test_load_descriptions_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"series": "../secret.txt"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="escapes"):
        load_descriptions(tmp_path / "manifest.json")
