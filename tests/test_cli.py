import tomllib
from pathlib import Path

import pytest

from avito_bridge import __version__
from avito_bridge.__main__ import _resolve_runtime_path, main


def test_resolve_runtime_path_anchors_relative_values(tmp_path):
    assert _resolve_runtime_path("feed_out/feed.xml", tmp_path) == (
        tmp_path / "feed_out" / "feed.xml"
    )
    absolute = (tmp_path / "absolute.xml").resolve()
    assert _resolve_runtime_path(str(absolute), tmp_path / "other") == absolute


def test_version_matches_project_metadata(capsys):
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert project["project"]["version"] == __version__ == "0.3.0"
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_uses_profile_root_and_reports_changed(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "profiles" / "test.yaml"
    config_path.parent.mkdir()
    config_path.write_text("placeholder", encoding="utf-8")
    cfg = type(
        "Cfg",
        (),
        {
            "bridge_root": tmp_path,
            "feed_path": "feed_out/profile.xml",
            "profile_name": "test",
        },
    )()
    captured = {}

    monkeypatch.setattr("avito_bridge.__main__.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "avito_bridge.__main__.fetch_profile_offers", lambda loaded: ["offer"]
    )

    def fake_cycle(provider, loaded, feed_path, state_path):
        captured.update(
            offers=provider(),
            feed_path=feed_path,
            state_path=state_path,
        )
        return type(
            "Result",
            (),
            {"offers_in": 1, "ads_built": 1, "skipped": 0, "changed": 1},
        )()

    monkeypatch.setattr("avito_bridge.__main__.run_cycle", fake_cycle)

    assert main(["--config", str(config_path)]) == 0
    assert captured["offers"] == ["offer"]
    assert captured["feed_path"] == tmp_path / "feed_out" / "profile.xml"
    assert captured["state_path"] == tmp_path / "state" / "state.db"
    output = capsys.readouterr().out
    assert "profile=test" in output
    assert "changed=1" in output
