"""Crash-consistent, profile-aware publication of a Studio archive.

Studio sends one configuration and one profile-owned description patch.  The
candidate is built against a merged copy of the live global description
manifest.  Only after validation succeeds are the patch, config and public
feed committed under a durable journal.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

import yaml
from defusedxml import ElementTree as ET

from avito_bridge.config import AppConfig, load_config
from avito_bridge.feed.validation import existing_ad_count, validate_count_drop
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.orchestrator.pipeline import run_cycle

_ALLOWED_ARCHIVE_ROOTS = frozenset({"config", "profiles", "description-patch"})
_PROFILE_CONFIG = re.compile(r"(?:config/config\.yaml|profiles/[A-Za-z0-9_.-]+\.yaml)\Z")
_PROFILE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_050
_MAX_DESCRIPTION_BYTES = 128 * 1024
_MAX_PATCH_ENTRIES = 10_000
_DESCRIPTION_MANIFEST = "avito-descriptions/manifest.json"
_DESCRIPTION_OWNERS = ".profile-owners.json"
_JOURNAL_NAME = "deployment.json"
_JOURNAL_SCHEMA = 1
_BACKUP_RETENTION = 20
_REQUIRED_AD_FIELDS = ("Id", "Title", "Description", "Price")
_PUBLISH_TARGETS = {
    "config/config.yaml": ("conditioners", "avito-feed.xml"),
    "profiles/wreaths.yaml": ("wreaths", "avito-feed-wreaths.xml"),
    "profiles/carver.yaml": ("carver", "avito-feed-carver.xml"),
}


@dataclass(frozen=True)
class PublishResult:
    profile: str
    offers_in: int
    ads_built: int
    skipped: int
    changed: int
    public_feed: Path
    backup_dir: Path


@dataclass(frozen=True)
class DescriptionPatch:
    profile: str
    upserts: dict[str, str]
    deletions: tuple[str, ...]
    files: dict[str, Path]


@dataclass(frozen=True)
class LiveOperation:
    target: Path
    data: bytes | None  # None is a tombstone/delete.


def validate_config_rel(value: str) -> str:
    """Return a safe POSIX path for the supported config locations."""
    normalized = str(PurePosixPath(str(value).replace("\\", "/")))
    if not _PROFILE_CONFIG.fullmatch(normalized):
        raise ValueError(
            "Config must be config/config.yaml or a YAML file directly inside profiles/"
        )
    return normalized


def validate_publish_target(
    config_rel: str,
    cfg: AppConfig,
    public_root: Path,
) -> Path:
    """Bind every publishable config to one profile and public filename."""
    expected = _PUBLISH_TARGETS.get(config_rel)
    if expected is None:
        raise ValueError(f"Config is not allowed for publication: {config_rel}")
    expected_profile, expected_filename = expected
    if cfg.profile_name != expected_profile:
        raise ValueError(
            f"{config_rel} must declare profile.name={expected_profile!r}, "
            f"got {cfg.profile_name!r}"
        )
    if not cfg.public_feed_path:
        raise ValueError(f"Profile {cfg.profile_name!r} has no public_feed_path")
    actual = Path(cfg.public_feed_path).resolve()
    required = (Path(public_root).resolve() / expected_filename).resolve()
    if actual != required:
        raise ValueError(
            f"{config_rel} may publish only to {required}, got {actual}"
        )
    return actual


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe archive path: {name!r}")
    if path.parts[0] not in _ALLOWED_ARCHIVE_ROOTS:
        raise ValueError(f"Unexpected archive root: {path.parts[0]!r}")
    return path


def safe_extract_archive(archive: Path, destination: Path) -> None:
    """Extract regular files/directories only, with traversal and size guards."""
    archive = Path(archive)
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ValueError("Studio deployment archive exceeds the 50 MiB safety limit")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    total_size = 0
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > _MAX_ARCHIVE_MEMBERS:
            raise ValueError("Studio deployment archive contains too many members")
        seen_names: set[PurePosixPath] = set()
        for member in members:
            safe_name = _safe_archive_name(member.name)
            if safe_name in seen_names:
                raise ValueError(f"Duplicate archive member: {member.name!r}")
            seen_names.add(safe_name)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"Archive links/devices are forbidden: {member.name!r}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported archive member: {member.name!r}")
            total_size += max(0, member.size)
            if total_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("Studio deployment archive exceeds the 50 MiB safety limit")

        for member in members:
            relative = _safe_archive_name(member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"Cannot read archive member: {member.name!r}")
            _write_atomic_bytes(target, source.read())


def validate_feed(path: Path, expected_ads: int, minimum_ads: int) -> None:
    """Validate the candidate before it can replace the last known-good feed."""
    if expected_ads < max(1, minimum_ads):
        raise ValueError(
            f"Candidate has {expected_ads} ads; required minimum is {max(1, minimum_ads)}"
        )
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Candidate feed is not valid XML: {exc}") from exc
    if root.tag != "Ads":
        raise ValueError(f"Candidate root must be Ads, got {root.tag!r}")
    ads = root.findall("Ad")
    if len(ads) != expected_ads:
        raise ValueError(
            f"Candidate result says {expected_ads} ads but XML contains {len(ads)}"
        )
    seen: set[str] = set()
    for index, ad in enumerate(ads, start=1):
        for tag in _REQUIRED_AD_FIELDS:
            if not str(ad.findtext(tag, "")).strip():
                raise ValueError(f"Ad {index} has no required {tag} field")
        ad_id = str(ad.findtext("Id", "")).strip()
        if ad_id in seen:
            raise ValueError(f"Candidate contains duplicate Id {ad_id!r}")
        seen.add(ad_id)
        image = ad.find("Images/Image")
        image_url = "" if image is None else str(image.get("url", "") or image.text or "")
        if not image_url.strip():
            raise ValueError(f"Ad {index} has no Images/Image URL")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic_bytes(target: Path, data: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Cross-platform advisory lock used by the timer and Studio publisher."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    acquired = False
    try:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError) as exc:
            raise RuntimeError(
                "Another Avito Bridge publication is already running"
            ) from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _safe_description_filename(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Description patch filenames must be non-empty strings")
    path = PurePosixPath(value.replace("\\", "/"))
    if len(path.parts) != 1 or path.name != value or path.suffix.lower() != ".txt":
        raise ValueError(f"Unsafe description filename: {value!r}")
    return value


def _safe_series_key(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("Description patch keys must be non-empty bounded strings")
    return value


def _validate_archive_layout(stage: Path, config_rel: str) -> None:
    files = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file()
    }
    required = {config_rel, "description-patch/patch.json"}
    if not required.issubset(files):
        missing = ", ".join(sorted(required - files))
        raise ValueError(f"Archive is missing required file(s): {missing}")
    for name in files:
        if name in required:
            continue
        path = PurePosixPath(name)
        if (
            len(path.parts) == 3
            and path.parts[:2] == ("description-patch", "files")
            and path.suffix.lower() == ".txt"
        ):
            continue
        raise ValueError(f"Unexpected archive file: {name!r}")


def _load_description_patch(
    stage: Path,
    expected_profile: str,
) -> DescriptionPatch:
    patch_path = stage / "description-patch" / "patch.json"
    if patch_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Description patch metadata is too large")
    try:
        raw = json.loads(patch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Description patch is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Unsupported description patch schema")
    if set(raw) != {
        "schema_version",
        "profile",
        "upserts",
        "deletions",
        "files_sha256",
    }:
        raise ValueError("Description patch contains unknown or missing fields")
    profile = raw.get("profile")
    if (
        not isinstance(profile, str)
        or not _PROFILE_NAME.fullmatch(profile)
        or profile != expected_profile
    ):
        raise ValueError(
            f"Description patch profile must be {expected_profile!r}, got {profile!r}"
        )
    raw_upserts = raw.get("upserts")
    raw_deletions = raw.get("deletions")
    raw_hashes = raw.get("files_sha256")
    if (
        not isinstance(raw_upserts, dict)
        or not isinstance(raw_deletions, list)
        or not isinstance(raw_hashes, dict)
    ):
        raise ValueError("Description patch upserts/deletions have invalid types")
    if len(raw_upserts) + len(raw_deletions) > _MAX_PATCH_ENTRIES:
        raise ValueError("Description patch contains too many entries")

    upserts: dict[str, str] = {}
    for raw_key, raw_filename in raw_upserts.items():
        key = _safe_series_key(raw_key)
        filename = _safe_description_filename(raw_filename)
        upserts[key] = filename
    deletions = tuple(_safe_series_key(key) for key in raw_deletions)
    if len(deletions) != len(set(deletions)):
        raise ValueError("Description patch contains duplicate tombstones")
    overlap = set(upserts).intersection(deletions)
    if overlap:
        raise ValueError(
            f"Description patch both updates and deletes: {sorted(overlap)[0]!r}"
        )

    files_dir = stage / "description-patch" / "files"
    actual_files = {
        path.name: path
        for path in files_dir.iterdir()
        if path.is_file()
    } if files_dir.is_dir() else {}
    expected_files = set(upserts.values())
    if set(raw_hashes) != expected_files:
        raise ValueError("Description patch hashes do not match referenced files")
    if set(actual_files) != expected_files:
        missing = sorted(expected_files - set(actual_files))
        extra = sorted(set(actual_files) - expected_files)
        raise ValueError(
            f"Description patch files do not match metadata; "
            f"missing={missing}, extra={extra}"
        )
    for filename, path in actual_files.items():
        if path.stat().st_size > _MAX_DESCRIPTION_BYTES:
            raise ValueError(f"Description file is too large: {filename!r}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ValueError(
                f"Description file is not UTF-8: {filename!r}"
            ) from exc
        if not text.strip():
            raise ValueError(f"Description file is empty: {filename!r}")
        expected_hash = raw_hashes[filename]
        if (
            not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
        ):
            raise ValueError(f"Description file hash mismatch: {filename!r}")
    return DescriptionPatch(profile, upserts, deletions, actual_files)


def _load_description_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Live description manifest is too large")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Live description manifest is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Live description manifest must be a JSON object")
    result: dict[str, str] = {}
    for raw_key, raw_filename in raw.items():
        result[_safe_series_key(raw_key)] = _safe_description_filename(raw_filename)
    return result


def _load_description_owners(
    live_root: Path,
    live_manifest: Mapping[str, str],
) -> dict[str, str]:
    path = live_root / _DESCRIPTION_OWNERS
    if not path.exists():
        # Explicit migration contract: before business profiles existed, the
        # global manifest was exclusively the conditioners catalogue.
        return {key: "conditioners" for key in live_manifest}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Description ownership manifest is too large")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Description ownership manifest is invalid: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "owners"}
        or raw.get("schema_version") != 1
        or not isinstance(raw.get("owners"), dict)
    ):
        raise ValueError("Description ownership manifest has an invalid schema")
    owners: dict[str, str] = {}
    for raw_key, raw_profile in raw["owners"].items():
        key = _safe_series_key(raw_key)
        if (
            not isinstance(raw_profile, str)
            or not _PROFILE_NAME.fullmatch(raw_profile)
        ):
            raise ValueError(f"Invalid description owner for {key!r}")
        owners[key] = raw_profile
    # A legacy writer may have added a manifest key after the ownership file
    # was created.  Preserve the same fail-closed conditioners migration rule.
    for key in live_manifest:
        owners.setdefault(key, "conditioners")
    return owners


def _description_bytes(path: Path, filename: str) -> bytes:
    if not path.is_file():
        raise ValueError(f"Live description file is missing: {filename!r}")
    if path.stat().st_size > _MAX_DESCRIPTION_BYTES:
        raise ValueError(f"Live description file is too large: {filename!r}")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"Live description file is not UTF-8: {filename!r}") from exc
    if not text.strip():
        raise ValueError(f"Live description file is empty: {filename!r}")
    return data


def _prepare_effective_descriptions(
    stage: Path,
    bridge_root: Path,
    patch: DescriptionPatch,
) -> tuple[dict[str, str], dict[str, str], set[str], bytes, bytes]:
    live_root = bridge_root / "avito-descriptions"
    live_manifest = _load_description_manifest(live_root / "manifest.json")
    owners = _load_description_owners(live_root, live_manifest)
    for key in [*patch.upserts, *patch.deletions]:
        owner = owners.get(key)
        if owner is not None and owner != patch.profile:
            raise ValueError(
                f"Description {key!r} is owned by profile {owner!r}, "
                f"not {patch.profile!r}"
            )
        owners[key] = patch.profile
    effective_root = stage / "avito-descriptions"
    effective_root.mkdir(parents=True, exist_ok=True)
    for filename in sorted(set(live_manifest.values())):
        _write_atomic_bytes(
            effective_root / filename,
            _description_bytes(live_root / filename, filename),
        )

    merged = dict(live_manifest)
    for key in patch.deletions:
        merged.pop(key, None)
    for key, filename in patch.upserts.items():
        conflicting_keys = [
            other_key
            for other_key, other_filename in merged.items()
            if other_key != key and other_filename == filename
        ]
        explicitly_shared_by_owner = all(
            owners.get(other_key) == patch.profile
            and patch.upserts.get(other_key) == filename
            for other_key in conflicting_keys
        )
        if conflicting_keys and not explicitly_shared_by_owner:
            raise ValueError(
                f"Description filename {filename!r} is already owned by "
                f"{conflicting_keys[0]!r}"
            )
        merged[key] = filename
        _write_atomic_bytes(
            effective_root / filename,
            patch.files[filename].read_bytes(),
        )
    manifest_bytes = (
        json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    owners_bytes = (
        json.dumps(
            {"schema_version": 1, "owners": owners},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _write_atomic_bytes(effective_root / "manifest.json", manifest_bytes)
    orphaned = set(live_manifest.values()) - set(merged.values())
    return live_manifest, merged, orphaned, manifest_bytes, owners_bytes


def _validate_candidate_manifest_setting(candidate_config: Path) -> None:
    try:
        raw = yaml.safe_load(candidate_config.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValueError(f"Candidate config is invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Candidate config must be a YAML object")
    content = raw.get("content") or {}
    if not isinstance(content, dict):
        raise ValueError("Candidate content config must be a YAML object")
    if content.get("descriptions_manifest") != _DESCRIPTION_MANIFEST:
        raise ValueError(
            "Publishable config must use "
            f"content.descriptions_manifest={_DESCRIPTION_MANIFEST!r}"
        )


def _allowed_journal_target(
    target: Path,
    bridge_root: Path,
    public_root: Path,
) -> bool:
    return (
        _is_within(target, bridge_root / "config")
        or _is_within(target, bridge_root / "profiles")
        or _is_within(target, bridge_root / "avito-descriptions")
        or _is_within(target, public_root)
    )


def _journal_bytes(journal: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(journal, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _prepare_transaction(
    operations: list[LiveOperation],
    backup_dir: Path,
    backup_root: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist every original and a prepared journal before the first live write."""
    backup_dir.mkdir(parents=True, exist_ok=False)
    _fsync_directory(backup_dir.parent)
    entries: list[dict[str, Any]] = []
    for operation in operations:
        target = operation.target.resolve()
        backup: str | None = None
        if target.exists():
            relative = target.relative_to(backup_root.resolve())
            backup_path = backup_dir / relative
            _write_atomic_bytes(backup_path, target.read_bytes())
            backup = relative.as_posix()
        entries.append(
            {
                "target": str(target),
                "backup": backup,
                "action": "delete" if operation.data is None else "write",
            }
        )
    journal = {
        "schema_version": _JOURNAL_SCHEMA,
        "status": "prepared",
        "prepared_at": time.time(),
        **dict(metadata),
        "backup_root": str(backup_root.resolve()),
        "operations": entries,
    }
    _write_atomic_bytes(backup_dir / _JOURNAL_NAME, _journal_bytes(journal))
    _fsync_directory(backup_dir)
    return journal


def _set_journal_status(
    backup_dir: Path,
    journal: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    updated = dict(journal)
    updated["status"] = status
    updated[f"{status}_at"] = time.time()
    _write_atomic_bytes(backup_dir / _JOURNAL_NAME, _journal_bytes(updated))
    return updated


def _restore_journal(
    backup_dir: Path,
    journal: Mapping[str, Any],
    bridge_root: Path,
    public_root: Path,
) -> None:
    if (
        journal.get("schema_version") != _JOURNAL_SCHEMA
        or journal.get("status") != "prepared"
        or not isinstance(journal.get("operations"), list)
    ):
        raise RuntimeError(f"Invalid prepared publication journal: {backup_dir}")
    errors: list[str] = []
    for entry in reversed(journal["operations"]):
        try:
            if not isinstance(entry, dict):
                raise ValueError("operation is not an object")
            target = Path(str(entry["target"])).resolve()
            if not _allowed_journal_target(target, bridge_root, public_root):
                raise ValueError(f"target is outside publication roots: {target}")
            backup_rel = entry.get("backup")
            if backup_rel is None:
                target.unlink(missing_ok=True)
                _fsync_directory(target.parent)
            else:
                relative = PurePosixPath(str(backup_rel).replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe backup path")
                backup = backup_dir.joinpath(*relative.parts).resolve()
                if not _is_within(backup, backup_dir) or not backup.is_file():
                    raise ValueError(f"backup is missing: {backup}")
                _write_atomic_bytes(target, backup.read_bytes())
        except Exception as exc:  # pragma: no cover - catastrophic/corrupt disk
            errors.append(str(exc))
    if errors:
        raise RuntimeError("Rollback failed: " + "; ".join(errors))


def _read_journal(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unreadable publication journal: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid publication journal: {path}")
    return value


def _recover_incomplete_transactions(
    backups_root: Path,
    bridge_root: Path,
    public_root: Path,
) -> None:
    """Roll back every durable prepared transaction while holding publish lock."""
    if not backups_root.is_dir():
        return
    for backup_dir in sorted(path for path in backups_root.iterdir() if path.is_dir()):
        journal_path = backup_dir / _JOURNAL_NAME
        # Missing journal means a crash happened before ``prepared`` and thus
        # before any live write.  It remains untouched for forensic recovery.
        if not journal_path.is_file():
            continue
        journal = _read_journal(journal_path)
        status = journal.get("status")
        if status == "prepared":
            _restore_journal(backup_dir, journal, bridge_root, public_root)
            _set_journal_status(backup_dir, journal, "rolled_back")
        elif status not in {"committed", "rolled_back"}:
            raise RuntimeError(
                f"Unknown publication journal status in {journal_path}: {status!r}"
            )


def _prune_completed_backups(
    backups_root: Path,
    *,
    keep: int = _BACKUP_RETENTION,
) -> None:
    """Retain newest completed backups; never remove prepared/incomplete ones."""
    if not backups_root.is_dir():
        return
    completed: list[tuple[float, Path]] = []
    for backup_dir in backups_root.iterdir():
        journal_path = backup_dir / _JOURNAL_NAME
        if not backup_dir.is_dir() or not journal_path.is_file():
            continue
        try:
            journal = _read_journal(journal_path)
        except RuntimeError:
            continue
        if journal.get("status") in {"committed", "rolled_back"}:
            completed.append((journal_path.stat().st_mtime, backup_dir))
    completed.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    for _, backup_dir in completed[max(0, keep):]:
        shutil.rmtree(backup_dir)


def _apply_operations(operations: list[LiveOperation]) -> None:
    for operation in operations:
        if operation.data is None:
            operation.target.unlink(missing_ok=True)
            _fsync_directory(operation.target.parent)
        else:
            _write_atomic_bytes(operation.target, operation.data)


def publish_archive(
    archive: Path,
    config_rel: str,
    bridge_root: Path,
    *,
    public_root: Path = Path("/opt/oasis/staticfiles"),
    offers_provider: Callable[[AppConfig], list] | None = None,
) -> PublishResult:
    """Build, validate, journal and promote one business profile."""
    config_rel = validate_config_rel(config_rel)
    expected = _PUBLISH_TARGETS.get(config_rel)
    if expected is None:
        raise ValueError(f"Config is not allowed for publication: {config_rel}")
    expected_profile = expected[0]
    bridge_root = Path(bridge_root).resolve()
    public_root = Path(public_root).resolve()
    state_root = bridge_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "profile-publish.lock"
    backups_root = state_root / "studio-backups"

    with _exclusive_lock(lock_path):
        _recover_incomplete_transactions(backups_root, bridge_root, public_root)
        _prune_completed_backups(backups_root)
        with tempfile.TemporaryDirectory(
            prefix="studio-candidate-", dir=state_root
        ) as raw_stage:
            stage = Path(raw_stage)
            safe_extract_archive(Path(archive), stage)
            _validate_archive_layout(stage, config_rel)
            candidate_config = stage.joinpath(*PurePosixPath(config_rel).parts)
            patch = _load_description_patch(stage, expected_profile)
            (
                live_manifest,
                merged_manifest,
                orphaned_files,
                manifest_bytes,
                owners_bytes,
            ) = _prepare_effective_descriptions(stage, bridge_root, patch)
            _validate_candidate_manifest_setting(candidate_config)

            cfg = load_config(candidate_config)
            public_feed = validate_publish_target(config_rel, cfg, public_root)
            candidate_feed = stage / "candidate-feed.xml"
            candidate_state = stage / "candidate-state.db"
            provider = offers_provider or fetch_profile_offers
            offers = provider(cfg)
            cycle = run_cycle(
                lambda: offers,
                cfg,
                feed_path=candidate_feed,
                state_path=candidate_state,
            )
            validate_feed(candidate_feed, cycle.ads_built, cfg.feed.min_active_ads)
            validate_count_drop(
                cycle.ads_built,
                existing_ad_count(public_feed),
                cfg.feed.max_drop_fraction,
            )

            live_config = bridge_root.joinpath(*PurePosixPath(config_rel).parts)
            live_descriptions = bridge_root / "avito-descriptions"
            operations: list[LiveOperation] = []
            for filename in sorted(set(patch.upserts.values())):
                operations.append(
                    LiveOperation(
                        live_descriptions / filename,
                        patch.files[filename].read_bytes(),
                    )
                )
            if patch.upserts or patch.deletions:
                operations.append(
                    LiveOperation(live_descriptions / "manifest.json", manifest_bytes)
                )
            # Ownership is part of the same journaled transaction.  It is
            # written even for an empty patch so legacy manifests are migrated
            # before any later profile can attempt to claim their keys.
            operations.append(
                LiveOperation(
                    live_descriptions / _DESCRIPTION_OWNERS,
                    owners_bytes,
                )
            )
            operations.extend(
                [
                    LiveOperation(live_config, candidate_config.read_bytes()),
                    LiveOperation(public_feed, candidate_feed.read_bytes()),
                ]
            )
            for filename in sorted(orphaned_files):
                operations.append(LiveOperation(live_descriptions / filename, None))

            targets = [operation.target.resolve() for operation in operations]
            if len(targets) != len(set(targets)):
                raise ValueError("Publication contains duplicate live targets")
            for target in targets:
                if not _allowed_journal_target(target, bridge_root, public_root):
                    raise ValueError(f"Live target escapes publication roots: {target}")

            backup_dir = (
                backups_root
                / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            )
            common_root = Path(
                os.path.commonpath([str(bridge_root), str(public_root)])
            ).resolve()
            metadata = {
                "config": config_rel,
                "profile": cfg.profile_name,
                "ads_built": cycle.ads_built,
                "public_feed": str(public_feed),
                "description_upserts": len(patch.upserts),
                "description_deletions": len(patch.deletions),
                "live_description_count_before": len(live_manifest),
                "live_description_count_after": len(merged_manifest),
            }
            journal = _prepare_transaction(
                operations, backup_dir, common_root, metadata
            )
            try:
                _apply_operations(operations)
            except Exception:
                _restore_journal(
                    backup_dir, journal, bridge_root, public_root
                )
                _set_journal_status(backup_dir, journal, "rolled_back")
                raise
            _set_journal_status(backup_dir, journal, "committed")
            _prune_completed_backups(backups_root)

            return PublishResult(
                profile=cfg.profile_name or Path(config_rel).stem,
                offers_in=cycle.offers_in,
                ads_built=cycle.ads_built,
                skipped=cycle.skipped,
                changed=cycle.changed,
                public_feed=public_feed,
                backup_dir=backup_dir,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely publish one Avito profile")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--bridge-root", default="/opt/avito-bridge")
    parser.add_argument("--public-root", default="/opt/oasis/staticfiles")
    args = parser.parse_args(argv)
    result = publish_archive(
        Path(args.archive),
        args.config,
        Path(args.bridge_root),
        public_root=Path(args.public_root),
    )
    print(
        f"profile={result.profile} offers_in={result.offers_in} "
        f"ads_built={result.ads_built} skipped={result.skipped} "
        f"changed={result.changed} public_feed={result.public_feed} "
        f"backup={result.backup_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
