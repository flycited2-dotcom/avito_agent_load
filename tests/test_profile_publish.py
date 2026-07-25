from __future__ import annotations

import io
import hashlib
import json
import os
import tarfile
from decimal import Decimal
from pathlib import Path

import pytest

from avito_bridge.models import Offer
from avito_bridge.profile_publish import (
    PublishResult,
    main,
    publish_archive,
    safe_extract_archive,
    validate_config_rel,
)


def _config(public_feed: Path, *, minimum: int = 1) -> str:
    return (
        "profile:\n"
        "  name: conditioners\n"
        "  source: oasis_db\n"
        "  grouping: per_item\n"
        "  feed_path: feed_out/test.xml\n"
        f"  public_feed_path: {public_feed.as_posix()}\n"
        "cities:\n"
        "  - {id: simferopol, name: Simferopol, avito_location: Simferopol}\n"
        "pricing: {rounding: none, default_markup_pct: 5, min_margin_abs: 0, rules: []}\n"
        "feed:\n"
        "  max_active_ads: 10\n"
        f"  min_active_ads: {minimum}\n"
        "  base_tags: {Category: Test, Condition: New}\n"
        "content: {title_max: 50, description_max: 7000, stop_words: [], "
        "descriptions_manifest: avito-descriptions/manifest.json}\n"
        "catalog: {selected_series: []}\n"
        "cards: {enabled: false, require_for_publish: false}\n"
    )


def _offer() -> Offer:
    return Offer(
        supplier_sku="manual:test-1",
        source="manual",
        brand="Test",
        model="Safe product",
        cost=Decimal("1000"),
        stock=1,
        photos=["https://example.test/image.jpg"],
    )


def _archive(
    tmp_path: Path,
    config_text: str,
    *,
    profile: str = "conditioners",
    config_rel: str = "config/config.yaml",
    upserts: dict[str, tuple[str, str]] | None = None,
    deletions: list[str] | None = None,
) -> Path:
    source = tmp_path / "source"
    config_path = source.joinpath(*config_rel.split("/"))
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config_text, encoding="utf-8")
    patch_root = source / "description-patch"
    (patch_root / "files").mkdir(parents=True)
    upserts = upserts or {}
    patch = {
        "schema_version": 1,
        "profile": profile,
        "upserts": {key: filename for key, (filename, _text) in upserts.items()},
        "deletions": deletions or [],
        "files_sha256": {
            filename: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for filename, text in upserts.values()
        },
    }
    (patch_root / "patch.json").write_text(
        json.dumps(patch), encoding="utf-8"
    )
    for filename, text in upserts.values():
        (patch_root / "files" / filename).write_text(text, encoding="utf-8")
    archive = tmp_path / "deploy.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(config_path, arcname=config_rel)
        bundle.add(patch_root / "patch.json", arcname="description-patch/patch.json")
        for path in (patch_root / "files").iterdir():
            bundle.add(path, arcname=f"description-patch/files/{path.name}")
    return archive


def test_publish_archive_builds_candidate_then_promotes_with_backup(tmp_path):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    public_feed = public / "avito-feed.xml"
    public_feed.write_text("<Ads>last-good</Ads>", encoding="utf-8")
    (bridge / "config").mkdir(parents=True)
    live_config = bridge / "config" / "config.yaml"
    live_config.write_text("old: config\n", encoding="utf-8")
    archive = _archive(tmp_path, _config(public_feed))

    result = publish_archive(
        archive,
        "config/config.yaml",
        bridge,
        public_root=public,
        offers_provider=lambda _cfg: [_offer()],
    )

    assert result.profile == "conditioners"
    assert result.ads_built == 1
    assert "profile:" in live_config.read_text(encoding="utf-8")
    feed = public_feed.read_text(encoding="utf-8")
    assert "<Ads" in feed and "Safe product" in feed
    assert (result.backup_dir / "avito-bridge/config/config.yaml").read_text(
        encoding="utf-8"
    ) == "old: config\n"
    assert (result.backup_dir / "public/avito-feed.xml").read_text(
        encoding="utf-8"
    ) == "<Ads>last-good</Ads>"
    assert (result.backup_dir / "deployment.json").is_file()
    journal = json.loads(
        (result.backup_dir / "deployment.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "committed"


def test_failed_candidate_keeps_config_and_last_good_feed(tmp_path):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    public_feed = public / "avito-feed.xml"
    public_feed.write_text("last-good", encoding="utf-8")
    (bridge / "config").mkdir(parents=True)
    live_config = bridge / "config" / "config.yaml"
    live_config.write_text("old: config\n", encoding="utf-8")
    archive = _archive(tmp_path, _config(public_feed, minimum=2))

    with pytest.raises(ValueError, match="минимум|required minimum"):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: [_offer()],
        )

    assert live_config.read_text(encoding="utf-8") == "old: config\n"
    assert public_feed.read_text(encoding="utf-8") == "last-good"


def test_promotion_error_rolls_back_every_replaced_file(tmp_path, monkeypatch):
    import avito_bridge.profile_publish as publisher

    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    public_feed = public / "avito-feed.xml"
    public_feed.write_text("last-good", encoding="utf-8")
    (bridge / "config").mkdir(parents=True)
    live_config = bridge / "config" / "config.yaml"
    live_config.write_text("old: config\n", encoding="utf-8")
    archive = _archive(tmp_path, _config(public_feed))
    real_write = publisher._write_atomic_bytes
    failed = False

    def fail_public_once(target, data):
        nonlocal failed
        if Path(target) == public_feed and not failed:
            failed = True
            raise OSError("simulated disk failure")
        return real_write(target, data)

    monkeypatch.setattr(publisher, "_write_atomic_bytes", fail_public_once)
    with pytest.raises(OSError, match="simulated"):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: [_offer()],
        )

    assert live_config.read_text(encoding="utf-8") == "old: config\n"
    assert public_feed.read_text(encoding="utf-8") == "last-good"
    journals = list((bridge / "state" / "studio-backups").glob("*/deployment.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["status"] == "rolled_back"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda text, _public: text.replace(
                "name: conditioners", "name: wreaths"
            ),
            "profile.name='conditioners'",
        ),
        (
            lambda text, public: text.replace(
                (public / "avito-feed.xml").as_posix(),
                (public / "avito-feed-wreaths.xml").as_posix(),
            ),
            "may publish only",
        ),
    ],
)
def test_publish_rejects_cross_profile_target_before_fetching_offers(
    tmp_path, mutate, message
):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    expected_feed = public / "avito-feed.xml"
    archive = _archive(
        tmp_path,
        mutate(_config(expected_feed), public),
    )

    with pytest.raises(ValueError, match=message):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: pytest.fail(
                "identity must be validated before source access"
            ),
        )


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../escape.txt")
        payload = b"bad"
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Unsafe archive path"):
        safe_extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_rejects_duplicate_member_names(tmp_path):
    archive = tmp_path / "duplicate.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for payload in (b"first", b"second"):
            member = tarfile.TarInfo("config/config.yaml")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Duplicate archive member"):
        safe_extract_archive(archive, tmp_path / "out")


@pytest.mark.parametrize(
    "value",
    ["../config.yaml", "/etc/passwd", "profiles/nested/x.yaml", "profiles/x.txt"],
)
def test_validate_config_rel_rejects_unsafe_values(value):
    with pytest.raises(ValueError, match="Config must"):
        validate_config_rel(value)


def test_main_reports_publish_result(monkeypatch, capsys, tmp_path):
    result = PublishResult(
        profile="wreaths",
        offers_in=55,
        ads_built=54,
        skipped=1,
        changed=2,
        public_feed=tmp_path / "feed.xml",
        backup_dir=tmp_path / "backup",
    )
    monkeypatch.setattr(
        "avito_bridge.profile_publish.publish_archive", lambda *args, **kwargs: result
    )

    assert main(
        [
            "--archive",
            str(tmp_path / "a.tgz"),
            "--config",
            "profiles/wreaths.yaml",
            "--bridge-root",
            str(tmp_path),
            "--public-root",
            str(tmp_path),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "profile=wreaths" in output
    assert "ads_built=54" in output


def test_profile_patch_merges_global_manifest_and_candidate_sees_effective_texts(
    tmp_path
):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    public_feed = public / "avito-feed.xml"
    descriptions = bridge / "avito-descriptions"
    descriptions.mkdir(parents=True)
    (descriptions / "manifest.json").write_text(
        json.dumps(
            {
                "other-profile-key": "other.txt",
                "conditioners-old": "old.txt",
            }
        ),
        encoding="utf-8",
    )
    (descriptions / "other.txt").write_text("Другой бизнес", encoding="utf-8")
    (descriptions / "old.txt").write_text("Удалить", encoding="utf-8")
    (bridge / "config").mkdir()
    (bridge / "config" / "config.yaml").write_text(
        "old: config\n", encoding="utf-8"
    )
    archive = _archive(
        tmp_path,
        _config(public_feed),
        upserts={
            "conditioners-new": ("conditioners-new.txt", "Новый текст"),
        },
        deletions=["conditioners-old"],
    )

    def provider(cfg):
        assert cfg.content.descriptions == {
            "other-profile-key": "Другой бизнес",
            "conditioners-new": "Новый текст",
        }
        return [_offer()]

    publish_archive(
        archive,
        "config/config.yaml",
        bridge,
        public_root=public,
        offers_provider=provider,
    )

    merged = json.loads(
        (descriptions / "manifest.json").read_text(encoding="utf-8")
    )
    assert merged == {
        "conditioners-new": "conditioners-new.txt",
        "other-profile-key": "other.txt",
    }
    assert (descriptions / "other.txt").read_text(encoding="utf-8") == "Другой бизнес"
    assert (descriptions / "conditioners-new.txt").read_text(
        encoding="utf-8"
    ) == "Новый текст"
    assert not (descriptions / "old.txt").exists()
    owners = json.loads(
        (descriptions / ".profile-owners.json").read_text(encoding="utf-8")
    )["owners"]
    assert owners == {
        "conditioners-new": "conditioners",
        "conditioners-old": "conditioners",
        "other-profile-key": "conditioners",
    }


@pytest.mark.parametrize("operation", ["delete", "upsert"])
def test_server_rejects_foreign_profile_description_mutation(tmp_path, operation):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    descriptions = bridge / "avito-descriptions"
    descriptions.mkdir(parents=True)
    (descriptions / "manifest.json").write_text(
        json.dumps({"conditioners-key": "conditioners.txt"}),
        encoding="utf-8",
    )
    (descriptions / "conditioners.txt").write_text(
        "Защищённый текст", encoding="utf-8"
    )
    wreath_feed = public / "avito-feed-wreaths.xml"
    config_text = (
        _config(wreath_feed)
        .replace("name: conditioners", "name: wreaths")
        .replace("source: oasis_db", "source: ritualb2b_site")
    )
    kwargs = (
        {"deletions": ["conditioners-key"]}
        if operation == "delete"
        else {
            "upserts": {
                "conditioners-key": ("wreath-version.txt", "Чужая правка")
            }
        }
    )
    archive = _archive(
        tmp_path,
        config_text,
        profile="wreaths",
        config_rel="profiles/wreaths.yaml",
        **kwargs,
    )

    with pytest.raises(ValueError, match="owned by profile 'conditioners'"):
        publish_archive(
            archive,
            "profiles/wreaths.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: pytest.fail("source must not be accessed"),
        )
    assert json.loads(
        (descriptions / "manifest.json").read_text(encoding="utf-8")
    ) == {"conditioners-key": "conditioners.txt"}
    assert (descriptions / "conditioners.txt").read_text(
        encoding="utf-8"
    ) == "Защищённый текст"


def test_patch_profile_must_match_bound_config_before_source_access(tmp_path):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    archive = _archive(
        tmp_path,
        _config(public / "avito-feed.xml"),
        profile="wreaths",
    )

    with pytest.raises(ValueError, match="must be 'conditioners'"):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: pytest.fail("source must not be accessed"),
        )


def test_archive_rejects_global_manifest_instead_of_profile_patch(tmp_path):
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "config" / "config.yaml").write_text(
        _config(tmp_path / "public" / "avito-feed.xml"), encoding="utf-8"
    )
    (source / "avito-descriptions").mkdir()
    (source / "avito-descriptions" / "manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    archive = tmp_path / "legacy-global.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "config", arcname="config")
        bundle.add(source / "avito-descriptions", arcname="avito-descriptions")

    with pytest.raises(ValueError, match="Unexpected archive root"):
        safe_extract_archive(archive, tmp_path / "out")


def test_patch_rejects_unreferenced_extra_file(tmp_path):
    archive = _archive(
        tmp_path,
        _config(tmp_path / "public" / "avito-feed.xml"),
    )
    rewritten = tmp_path / "extra.tgz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(
        rewritten, "w:gz"
    ) as target:
        for member in source.getmembers():
            stream = source.extractfile(member) if member.isfile() else None
            target.addfile(member, stream)
        payload = b"not referenced"
        member = tarfile.TarInfo("description-patch/files/extra.txt")
        member.size = len(payload)
        target.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="do not match metadata"):
        publish_archive(
            rewritten,
            "config/config.yaml",
            tmp_path / "bridge",
            public_root=tmp_path / "public",
            offers_provider=lambda _cfg: pytest.fail("source must not be accessed"),
        )


def test_patch_cannot_reuse_filename_owned_by_another_manifest_key(tmp_path):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    descriptions = bridge / "avito-descriptions"
    descriptions.mkdir(parents=True)
    (descriptions / "manifest.json").write_text(
        json.dumps({"other-key": "shared.txt"}), encoding="utf-8"
    )
    (descriptions / "shared.txt").write_text("Другой текст", encoding="utf-8")
    archive = _archive(
        tmp_path,
        _config(public / "avito-feed.xml"),
        upserts={"new-key": ("shared.txt", "Попытка перезаписи")},
    )

    with pytest.raises(ValueError, match="already owned"):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=lambda _cfg: pytest.fail("source must not be accessed"),
        )
    assert (descriptions / "shared.txt").read_text(encoding="utf-8") == "Другой текст"


def test_same_owner_may_explicitly_update_all_keys_sharing_legacy_file(tmp_path):
    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    descriptions = bridge / "avito-descriptions"
    descriptions.mkdir(parents=True)
    (descriptions / "manifest.json").write_text(
        json.dumps({"first": "shared.txt", "second": "shared.txt"}),
        encoding="utf-8",
    )
    (descriptions / "shared.txt").write_text("Старый общий текст", encoding="utf-8")
    archive = _archive(
        tmp_path,
        _config(public / "avito-feed.xml"),
        upserts={
            "first": ("shared.txt", "Новый общий текст"),
            "second": ("shared.txt", "Новый общий текст"),
        },
    )

    publish_archive(
        archive,
        "config/config.yaml",
        bridge,
        public_root=public,
        offers_provider=lambda _cfg: [_offer()],
    )

    assert (descriptions / "shared.txt").read_text(
        encoding="utf-8"
    ) == "Новый общий текст"


def test_prepared_journal_exists_before_first_live_operation(tmp_path, monkeypatch):
    import avito_bridge.profile_publish as publisher

    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    (bridge / "config").mkdir(parents=True)
    (bridge / "config" / "config.yaml").write_text("old\n", encoding="utf-8")
    archive = _archive(tmp_path, _config(public / "avito-feed.xml"))
    real_apply = publisher._apply_operations
    observed = []

    def inspect_then_apply(operations):
        journals = list(
            (bridge / "state" / "studio-backups").glob("*/deployment.json")
        )
        assert len(journals) == 1
        observed.append(
            json.loads(journals[0].read_text(encoding="utf-8"))["status"]
        )
        real_apply(operations)

    monkeypatch.setattr(publisher, "_apply_operations", inspect_then_apply)
    publish_archive(
        archive,
        "config/config.yaml",
        bridge,
        public_root=public,
        offers_provider=lambda _cfg: [_offer()],
    )
    assert observed == ["prepared"]


def test_next_publish_recovers_sigkill_style_partial_commit_under_lock(tmp_path):
    import avito_bridge.profile_publish as publisher

    bridge = tmp_path / "avito-bridge"
    public = tmp_path / "public"
    public.mkdir()
    public_feed = public / "avito-feed.xml"
    public_feed.write_text("old feed", encoding="utf-8")
    live_config = bridge / "config" / "config.yaml"
    live_config.parent.mkdir(parents=True)
    live_config.write_text("old config\n", encoding="utf-8")
    backup_dir = bridge / "state" / "studio-backups" / "crashed"
    operations = [
        publisher.LiveOperation(live_config, b"partial new config\n"),
        publisher.LiveOperation(public_feed, b"partial new feed"),
    ]
    publisher._prepare_transaction(
        operations,
        backup_dir,
        tmp_path,
        {
            "config": "config/config.yaml",
            "profile": "conditioners",
            "ads_built": 1,
            "public_feed": str(public_feed),
        },
    )
    # Equivalent durable state after SIGKILL between two os.replace calls:
    publisher._write_atomic_bytes(live_config, b"partial new config\n")
    assert public_feed.read_text(encoding="utf-8") == "old feed"

    archive = _archive(
        tmp_path,
        _config(public_feed, minimum=2),
    )

    def provider(_cfg):
        # Recovery happens before candidate construction/source access.
        assert live_config.read_text(encoding="utf-8") == "old config\n"
        assert public_feed.read_text(encoding="utf-8") == "old feed"
        return [_offer()]

    with pytest.raises(ValueError, match="required minimum|минимум"):
        publish_archive(
            archive,
            "config/config.yaml",
            bridge,
            public_root=public,
            offers_provider=provider,
        )
    journal = json.loads(
        (backup_dir / "deployment.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "rolled_back"
    assert live_config.read_text(encoding="utf-8") == "old config\n"


def test_retention_removes_only_old_completed_backups(tmp_path):
    import avito_bridge.profile_publish as publisher

    root = tmp_path / "backups"
    root.mkdir()
    for index in range(23):
        directory = root / f"completed-{index:02d}"
        directory.mkdir()
        journal = {
            "schema_version": 1,
            "status": "committed",
            "operations": [],
        }
        path = directory / "deployment.json"
        path.write_text(json.dumps(journal), encoding="utf-8")
        timestamp = 1_000 + index
        os.utime(path, (timestamp, timestamp))
    prepared = root / "prepared"
    prepared.mkdir()
    (prepared / "deployment.json").write_text(
        json.dumps(
            {"schema_version": 1, "status": "prepared", "operations": []}
        ),
        encoding="utf-8",
    )
    incomplete = root / "incomplete"
    incomplete.mkdir()

    publisher._prune_completed_backups(root, keep=20)

    assert len(list(root.glob("completed-*"))) == 20
    assert prepared.is_dir()
    assert incomplete.is_dir()


def test_safe_extract_rejects_oversized_tgz_before_open(tmp_path, monkeypatch):
    import avito_bridge.profile_publish as publisher

    archive = tmp_path / "oversized.tgz"
    with archive.open("wb") as stream:
        stream.truncate(publisher._MAX_ARCHIVE_BYTES + 1)
    opened = False

    def must_not_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("tarfile.open must not be reached")

    monkeypatch.setattr(publisher.tarfile, "open", must_not_open)
    with pytest.raises(ValueError, match="50 MiB"):
        safe_extract_archive(archive, tmp_path / "out")
    assert opened is False
