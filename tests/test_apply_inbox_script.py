from __future__ import annotations

import io
import json
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
import yaml
from PIL import Image

from scripts import apply_inbox


def _config(*, existing: bool = False) -> str:
    photo = '    "OLD": "https://old.test/photo.jpg"\n' if existing else ""
    forced = '    "OLD": {price: 1, series: "Old"}\n' if existing else ""
    selected = '    - "__none__"\n' if existing else ""
    return (
        "catalog:\n"
        "  manual_photos:\n"
        f"{photo}"
        "  force_include:\n"
        f"{forced}"
        "  selected_series:\n"
        f"{selected}"
        "cards:\n"
        "  enabled: false\n"
    )


def _valid_item(**overrides):
    item = {
        "series_key": "supplier|brand|series",
        "photos": [],
        "description": None,
        "force_nc": None,
        "price": None,
        "series_name": None,
    }
    item.update(overrides)
    return item


def test_atomic_write_replaces_file_and_cleans_temporary(tmp_path):
    target = tmp_path / "nested" / "config.yaml"
    target.parent.mkdir()
    target.write_bytes(b"old")

    apply_inbox.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_atomic_write_failure_keeps_original_and_cleans_temporary(
    monkeypatch, tmp_path
):
    target = tmp_path / "config.yaml"
    target.write_bytes(b"last-good")
    monkeypatch.setattr(
        apply_inbox.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        apply_inbox.atomic_write(target, b"candidate")

    assert target.read_bytes() == b"last-good"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_ssh_run_quotes_each_argument_and_checks_return_code(
    monkeypatch, tmp_path
):
    calls = []
    key = tmp_path / "deploy-key"
    key.write_text("synthetic key", encoding="utf-8")
    monkeypatch.setenv(apply_inbox.SSH_HOST_ENV, "deploy@example.test")
    monkeypatch.setenv(apply_inbox.SSH_KEY_ENV, str(key))

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=b"done")

    monkeypatch.setattr(apply_inbox.subprocess, "run", fake_run)
    output = apply_inbox.ssh_run(
        ["tee", "--", "/tmp/feed; touch /tmp/not-created"], input_data=b"payload"
    )

    assert output == "done"
    remote = calls[0][0][-1]
    assert remote == "tee -- '/tmp/feed; touch /tmp/not-created'"
    assert calls[0][1]["input"] == b"payload"
    assert calls[0][1]["check"] is True
    assert calls[0][1]["timeout"] == 300
    with pytest.raises(ValueError, match="must not be empty"):
        apply_inbox.ssh_run([])


def test_ssh_base_requires_explicit_safe_host_and_existing_key(
    monkeypatch, tmp_path
):
    monkeypatch.delenv(apply_inbox.SSH_HOST_ENV, raising=False)
    monkeypatch.setenv(apply_inbox.SSH_KEY_ENV, str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match=apply_inbox.SSH_HOST_ENV):
        apply_inbox.ssh_base()

    monkeypatch.setenv(
        apply_inbox.SSH_HOST_ENV,
        "-oProxyCommand=touch bad",
    )
    with pytest.raises(RuntimeError, match="safe user@host"):
        apply_inbox.ssh_base()

    monkeypatch.setenv(apply_inbox.SSH_HOST_ENV, "deploy@example.test")
    with pytest.raises(RuntimeError, match=apply_inbox.SSH_KEY_ENV):
        apply_inbox.ssh_base()


def test_ssh_put_rejects_relative_path_and_uses_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(
        apply_inbox,
        "ssh_run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or "",
    )

    apply_inbox.ssh_put("/tmp/safe name", b"x")

    assert calls == [(["tee", "--", "/tmp/safe name"], {"input_data": b"x"})]
    with pytest.raises(ValueError, match="absolute POSIX"):
        apply_inbox.ssh_put("../escape", b"x")


@pytest.mark.parametrize(
    "value",
    ["../x", "a/b", "a\\b", ".hidden", "..", "x;rm", "", None],
)
def test_safe_leaf_rejects_paths_and_shell_metacharacters(value):
    with pytest.raises(ValueError, match="Unsafe"):
        apply_inbox.safe_leaf(value)


def test_safe_leaf_accepts_catalogue_code_and_checks_suffix():
    assert apply_inbox.safe_leaf("НС-1690797") == "НС-1690797"
    assert apply_inbox.safe_leaf("description.txt", suffix=".txt") == "description.txt"
    with pytest.raises(ValueError, match="Expected a .txt"):
        apply_inbox.safe_leaf("description.json", suffix=".txt")


def test_normalize_photo_outputs_rgb_jpeg(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (2, 3), (255, 0, 0, 128)).save(source)

    result = apply_inbox.normalize_photo(source)

    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (2, 3)


def test_normalize_photo_rejects_oversize_before_pillow(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.png"
    source.write_bytes(b"x")
    monkeypatch.setattr(apply_inbox, "MAX_PHOTO_BYTES", 0)
    monkeypatch.setattr(
        Image,
        "open",
        lambda *_args, **_kwargs: pytest.fail("oversized photo must not be decoded"),
    )

    with pytest.raises(ValueError, match="Photo size"):
        apply_inbox.normalize_photo(source)


def test_normalize_photo_rejects_excessive_dimensions(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), "white").save(source)
    monkeypatch.setattr(apply_inbox, "MAX_PHOTO_PIXELS", 3)

    with pytest.raises(ValueError, match="pixel safety limit"):
        apply_inbox.normalize_photo(source)


def test_find_photo_handles_missing_unique_and_ambiguous(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    assert apply_inbox.find_photo("NC-1", inbox=tmp_path) is None
    first = photos / "NC-1.png"
    first.write_bytes(b"x")
    assert apply_inbox.find_photo("NC-1", inbox=tmp_path) == first
    (photos / "NC-1.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="Several photos"):
        apply_inbox.find_photo("NC-1", inbox=tmp_path)


def test_upload_photo_uses_content_addressed_name_and_hidden_candidate(capsys):
    runs = []
    puts = []

    url = apply_inbox.upload_photo(
        "НС-1",
        b"jpeg",
        source_name="source.png",
        run=lambda argv: runs.append(argv) or "",
        put=lambda path, data: puts.append((path, data)),
    )

    filename = apply_inbox.photo_filename("НС-1", b"jpeg")
    remote = f"{apply_inbox.REMOTE_PHOTOS}/{filename}"
    assert runs[0] == ["mkdir", "-p", "--", apply_inbox.REMOTE_PHOTOS]
    temporary = puts[0][0]
    assert temporary.startswith(
        f"{apply_inbox.REMOTE_PHOTOS}/.{filename}."
    ) and temporary.endswith(".tmp")
    assert puts == [(temporary, b"jpeg")]
    assert runs[1] == ["chmod", "0644", "--", temporary]
    assert runs[2] == ["mv", "-f", "--", temporary, remote]
    assert runs[3] == ["rm", "-f", "--", temporary]
    assert url == apply_inbox.photo_public_url("НС-1", b"jpeg")
    assert "source.png" in capsys.readouterr().out


def test_content_addressed_upload_never_replaces_legacy_public_url():
    legacy = f"{apply_inbox.REMOTE_PHOTOS}/NC-1.jpg"
    remote_files = {legacy: b"last-known-good"}

    def put(path, data):
        remote_files[path] = data

    def run(argv):
        if argv[0] == "mv":
            source, target = argv[-2:]
            remote_files[target] = remote_files.pop(source)
        elif argv[0] == "rm":
            remote_files.pop(argv[-1], None)
        return ""

    url = apply_inbox.upload_photo(
        "NC-1",
        b"candidate",
        run=run,
        put=put,
    )

    expected_name = apply_inbox.photo_filename("NC-1", b"candidate")
    expected_remote = f"{apply_inbox.REMOTE_PHOTOS}/{expected_name}"
    assert remote_files[legacy] == b"last-known-good"
    assert remote_files[expected_remote] == b"candidate"
    assert url == f"{apply_inbox.PHOTO_BASE}/{expected_name}"


def test_photo_url_changes_when_normalized_bytes_change():
    first = apply_inbox.photo_public_url("NC-1", b"first")
    second = apply_inbox.photo_public_url("NC-1", b"second")

    assert first != second
    assert first.endswith(".jpg")
    assert second.endswith(".jpg")


def test_upload_photo_failure_cleans_temporary_and_does_not_promote():
    runs = []

    def fail_put(_path, _data):
        raise subprocess.CalledProcessError(1, "ssh")

    with pytest.raises(subprocess.CalledProcessError):
        apply_inbox.upload_photo(
            "NC-1",
            b"jpeg",
            run=lambda argv: runs.append(argv) or "",
            put=fail_put,
        )

    assert [command[0] for command in runs] == ["mkdir", "rm"]
    assert all(command[0] != "mv" for command in runs)


def test_validate_items_normalizes_valid_input_and_rejects_bad_shapes():
    items = apply_inbox.validate_items(
        [
            {
                "series_key": "  supplier|brand|series  ",
                "photos": ["НС-1"],
                "description": "series.txt",
            },
            {
                "force_nc": "НС-2",
                "price": 1234,
                "series_name": "  Forced  ",
            },
        ]
    )
    assert items[0]["series_key"] == "supplier|brand|series"
    assert items[1]["series_name"] == "Forced"
    assert apply_inbox.validate_items(None) == []

    invalid_values = [
        {},
        ["not-a-mapping"],
        [{"photos": "not-a-list"}],
        [{"description": "x.txt"}],
        [{"force_nc": "NC", "price": True}],
        [{"force_nc": "NC", "price": float("inf")}],
        [{"series_key": " "}],
        [{"series_name": 1}],
    ]
    for value in invalid_values:
        with pytest.raises(ValueError):
            apply_inbox.validate_items(value)


def test_load_items_reads_yaml_and_rejects_non_mapping(tmp_path):
    inbox = tmp_path / "inbox.yaml"
    inbox.write_text("items:\n  - series_key: a\n", encoding="utf-8")
    assert apply_inbox.load_items(inbox)[0]["series_key"] == "a"
    inbox.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="root"):
        apply_inbox.load_items(inbox)


def test_update_config_text_adds_and_replaces_structured_values():
    updated = apply_inbox.update_config_text(
        _config(existing=True),
        manual_photos={"OLD": "https://new.test/old.jpg", "NEW": "https://new.test/new.jpg"},
        force_include={
            "OLD": {"price": 2500, "series": "Updated"},
            "NEW": {"price": 3000},
        },
        selected_series=["supplier|brand|series", "supplier|brand|series"],
    )
    data = yaml.safe_load(updated)["catalog"]

    assert data["manual_photos"] == {
        "OLD": "https://new.test/old.jpg",
        "NEW": "https://new.test/new.jpg",
    }
    assert data["force_include"]["OLD"] == {"price": 2500, "series": "Updated"}
    assert data["force_include"]["NEW"] == {"price": 3000}
    assert data["selected_series"] == ["supplier|brand|series"]
    assert "__none__" not in updated


def test_update_config_text_rejects_missing_or_ambiguous_sections():
    with pytest.raises(ValueError, match="manual_photos"):
        apply_inbox.update_config_text(
            "catalog:\n",
            manual_photos={"NC": "url"},
            force_include={},
            selected_series=[],
        )


def test_prepare_writes_stages_config_manifest_and_description(tmp_path):
    config = tmp_path / "config" / "config.yaml"
    manifest = tmp_path / "avito-descriptions" / "manifest.json"
    inbox = tmp_path / "inbox"
    (inbox / "descriptions").mkdir(parents=True)
    config.parent.mkdir()
    manifest.parent.mkdir()
    config.write_text(_config(), encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    (inbox / "descriptions" / "new.txt").write_text("Описание", encoding="utf-8")
    items = [
        _valid_item(description="new.txt", photos=["NC-1"]),
        _valid_item(
            series_key=None,
            force_nc="NC-2",
            price=5000,
            series_name="Forced",
        ),
    ]

    writes = apply_inbox.prepare_writes(
        items,
        {"NC-1": "https://example.test/NC-1.jpg"},
        root=tmp_path,
        config_path=config,
        manifest_path=manifest,
        inbox=inbox,
    )

    assert writes[tmp_path / "avito-descriptions" / "new.txt"] == "Описание".encode()
    new_manifest = json.loads(writes[manifest.resolve()])
    assert new_manifest["supplier|brand|series"] == "new.txt"
    new_config = yaml.safe_load(writes[config.resolve()])
    assert new_config["catalog"]["manual_photos"]["NC-1"].endswith("NC-1.jpg")
    assert new_config["catalog"]["force_include"]["NC-2"]["price"] == 5000


def test_prepare_writes_rejects_invalid_manifest(tmp_path):
    config = tmp_path / "config.yaml"
    manifest = tmp_path / "manifest.json"
    config.write_text(_config(), encoding="utf-8")
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        apply_inbox.prepare_writes(
            [],
            {},
            root=tmp_path,
            config_path=config,
            manifest_path=manifest,
            inbox=tmp_path,
        )


def test_prepare_writes_missing_description_leaves_disk_unchanged(tmp_path):
    config = tmp_path / "config.yaml"
    manifest = tmp_path / "manifest.json"
    config.write_text(_config(), encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        apply_inbox.prepare_writes(
            [_valid_item(description="missing.txt")],
            {},
            root=tmp_path,
            config_path=config,
            manifest_path=manifest,
            inbox=tmp_path / "inbox",
        )

    assert config.read_text(encoding="utf-8") == _config()
    assert manifest.read_text(encoding="utf-8") == "{}"


def test_apply_transaction_commits_and_rolls_back_new_and_existing_files(tmp_path):
    existing = tmp_path / "existing"
    created = tmp_path / "created"
    existing.write_bytes(b"old")
    apply_inbox.apply_transaction({existing: b"new", created: b"created"})
    assert existing.read_bytes() == b"new"
    assert created.read_bytes() == b"created"

    existing.write_bytes(b"old-again")
    created.unlink()
    with pytest.raises(RuntimeError, match="action failed"):
        apply_inbox.apply_transaction(
            {existing: b"bad", created: b"bad"},
            lambda: (_ for _ in ()).throw(RuntimeError("action failed")),
        )
    assert existing.read_bytes() == b"old-again"
    assert not created.exists()


def test_apply_transaction_reports_incomplete_rollback(monkeypatch, tmp_path):
    target = tmp_path / "config"
    target.write_bytes(b"last-good")
    real_atomic_write = apply_inbox.atomic_write

    def fail_restore(path, data):
        if data == b"last-good":
            raise OSError("disk failed during rollback")
        real_atomic_write(path, data)

    monkeypatch.setattr(apply_inbox, "atomic_write", fail_restore)
    with pytest.raises(RuntimeError, match="rollback was incomplete"):
        apply_inbox.apply_transaction(
            {target: b"candidate"},
            lambda: (_ for _ in ()).throw(RuntimeError("publish failed")),
        )
    assert target.read_bytes() == b"candidate"


def test_build_archive_contains_only_expected_roots(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("x: 1", encoding="utf-8")
    (tmp_path / "avito-descriptions").mkdir()
    (tmp_path / "avito-descriptions" / "manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "secret.env").write_text("secret", encoding="utf-8")

    payload = apply_inbox.build_archive(root=tmp_path)

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        names = archive.getnames()
    assert "config/config.yaml" in names
    assert "description-patch/patch.json" in names
    assert not any(name.startswith("avito-descriptions/") for name in names)
    assert all(not name.endswith("secret.env") for name in names)


def test_build_archive_includes_only_explicit_inbox_description_keys(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("x: 1", encoding="utf-8")
    descriptions = tmp_path / "avito-descriptions"
    descriptions.mkdir()
    (descriptions / "manifest.json").write_text(
        json.dumps(
            {
                "conditioners-key": "conditioners.txt",
                "other-profile-key": "other.txt",
            }
        ),
        encoding="utf-8",
    )
    (descriptions / "conditioners.txt").write_text(
        "Кондиционеры", encoding="utf-8"
    )
    (descriptions / "other.txt").write_text("Другой бизнес", encoding="utf-8")

    payload = apply_inbox.build_archive(
        root=tmp_path,
        description_keys=["conditioners-key"],
    )

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        patch = json.loads(
            archive.extractfile("description-patch/patch.json")
            .read()
            .decode("utf-8")
        )
        names = archive.getnames()
    assert set(patch["upserts"]) == {"conditioners-key"}
    assert "description-patch/files/conditioners.txt" in names
    assert "description-patch/files/other.txt" not in names


def test_deploy_invokes_safe_publisher_and_always_cleans_remote(monkeypatch):
    puts = []
    runs = []
    monkeypatch.setattr(
        apply_inbox, "ssh_put", lambda path, data: puts.append((path, data))
    )
    monkeypatch.setattr(
        apply_inbox, "ssh_run", lambda argv, **kwargs: runs.append(argv) or ""
    )

    apply_inbox.deploy(b"archive")

    assert puts[0][1] == b"archive"
    assert runs[0] == [
        "install", "-d", "-m", "700", "--", apply_inbox.REMOTE_UPLOAD_DIR
    ]
    assert runs[1][:3] == ["sh", "-eu", "-c"]
    assert "avito_bridge.profile_publish" in runs[1][3]
    assert f"PYTHONPATH={apply_inbox.REMOTE_ROOT}/src" in runs[1][3]
    assert "--config config/config.yaml" in runs[1][3]
    assert runs[-1][:3] == ["rm", "-f", "--"]


def test_deploy_propagates_publish_failure_but_ignores_cleanup_failure(monkeypatch):
    monkeypatch.setattr(apply_inbox, "ssh_put", lambda *_args: None)
    calls = 0

    def fail(_argv, **_kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.CalledProcessError(1, "ssh")

    monkeypatch.setattr(apply_inbox, "ssh_run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        apply_inbox.deploy(b"archive")
    assert calls == 1


def test_main_empty_and_local_modes_never_contact_ssh(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(apply_inbox, "load_items", lambda _path: [])
    assert apply_inbox.main(["--inbox", str(tmp_path / "empty.yaml")]) == 0
    assert "нечего" in capsys.readouterr().out

    target = tmp_path / "target"
    monkeypatch.setattr(
        apply_inbox,
        "load_items",
        lambda _path: [_valid_item(photos=[])],
    )
    monkeypatch.setattr(
        apply_inbox, "prepare_writes", lambda *_args, **_kwargs: {target: b"safe"}
    )
    monkeypatch.setattr(
        apply_inbox,
        "upload_photo",
        lambda *_args, **_kwargs: pytest.fail("local mode must not upload"),
    )
    monkeypatch.setattr(
        apply_inbox,
        "deploy",
        lambda *_args: pytest.fail("local mode must not deploy"),
    )

    assert apply_inbox.main(["--inbox", str(tmp_path / "inbox.yaml")]) == 0
    assert target.read_bytes() == b"safe"
    assert "Локальный режим" in capsys.readouterr().out


def test_main_fails_closed_when_requested_photo_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        apply_inbox,
        "load_items",
        lambda _path: [_valid_item(photos=["NC-MISSING"])],
    )
    monkeypatch.setattr(apply_inbox, "find_photo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        apply_inbox,
        "prepare_writes",
        lambda *_args, **_kwargs: pytest.fail("must validate photos first"),
    )

    with pytest.raises(FileNotFoundError, match="NC-MISSING"):
        apply_inbox.main(["--inbox", str(tmp_path / "inbox.yaml")])


def test_main_deploy_mode_uploads_then_runs_transaction_action(
    monkeypatch, tmp_path
):
    source = tmp_path / "photos" / "NC-1.png"
    source.parent.mkdir()
    source.write_bytes(b"source")
    target = tmp_path / "target"
    events = []
    monkeypatch.setattr(
        apply_inbox,
        "load_items",
        lambda _path: [_valid_item(photos=["NC-1"])],
    )
    monkeypatch.setattr(apply_inbox, "find_photo", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(apply_inbox, "normalize_photo", lambda _path: b"jpeg")
    monkeypatch.setattr(
        apply_inbox,
        "prepare_writes",
        lambda *_args, **_kwargs: {target: b"config"},
    )
    monkeypatch.setattr(
        apply_inbox,
        "upload_photo",
        lambda nc, data, **_kwargs: (
            events.append(("upload", nc, data))
            or apply_inbox.photo_public_url(nc, data)
        ),
    )
    monkeypatch.setattr(
        apply_inbox, "build_archive", lambda **_kwargs: b"archive"
    )
    monkeypatch.setattr(
        apply_inbox, "deploy", lambda data: events.append(("deploy", data))
    )

    assert (
        apply_inbox.main(
            ["--inbox", str(tmp_path / "inbox.yaml"), "--deploy"]
        )
        == 0
    )
    assert events == [("upload", "NC-1", b"jpeg"), ("deploy", b"archive")]
    assert target.read_bytes() == b"config"
