#!/usr/bin/env python3
"""Safely apply ``inbox/inbox.yaml`` to the conditioners profile.

The local config, description manifest and description files are committed as
one recoverable transaction.  Remote publication is opt-in (``--deploy``) and
uses :mod:`avito_bridge.profile_publish`; the archive is never extracted over
the live checkout.

Run from the repository root::

    python scripts/apply_inbox.py
    python scripts/apply_inbox.py --deploy

Photo conversion uses the runtime Pillow dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
import uuid
import warnings
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
CFG = ROOT / "config" / "config.yaml"
MANIFEST = ROOT / "avito-descriptions" / "manifest.json"
SSH_HOST_ENV = "AVITO_BRIDGE_SSH_HOST"
SSH_KEY_ENV = "AVITO_BRIDGE_SSH_KEY"
REMOTE_ROOT = "/opt/avito-bridge"
REMOTE_PUBLIC_ROOT = "/opt/oasis/staticfiles"
REMOTE_PHOTOS = "/opt/oasis/staticfiles/manual-photos"
REMOTE_UPLOAD_DIR = f"{REMOTE_ROOT}/runtime/inbox-uploads"
PHOTO_BASE = "https://splithome.ru/static/manual-photos"
PROFILE_CONFIG = "config/config.yaml"

_SAFE_LEAF = re.compile(r"[\w.-]{1,120}\Z", re.UNICODE)
_SAFE_SSH_HOST = re.compile(
    r"(?:[A-Za-z0-9_.][A-Za-z0-9_.-]*@)?"
    r"(?:[A-Za-z0-9_.][A-Za-z0-9_.:-]*)\Z"
)
MAX_PHOTO_BYTES = 25 * 1024 * 1024
MAX_PHOTO_PIXELS = 40_000_000


def atomic_write(path: Path, data: bytes) -> None:
    """Replace *path* atomically and fsync the staged contents first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ssh_base() -> list[str]:
    """Build an explicit SSH command without embedding production addresses."""
    host = os.environ.get(SSH_HOST_ENV, "").strip()
    if not host or host.startswith("-") or not _SAFE_SSH_HOST.fullmatch(host):
        raise RuntimeError(
            f"Set {SSH_HOST_ENV} to a safe user@host value before --deploy"
        )
    key = Path(
        os.environ.get(
            SSH_KEY_ENV,
            str(Path.home() / ".ssh" / "climat_simf_deploy"),
        )
    ).expanduser()
    if not key.is_file():
        raise RuntimeError(
            f"SSH key not found; set {SSH_KEY_ENV}: {key}"
        )
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=45",
        host,
    ]


def ssh_run(command: Sequence[str], *, input_data: bytes | None = None) -> str:
    """Run a quoted remote argv and raise when SSH or the command fails."""
    if not command:
        raise ValueError("Remote command must not be empty")
    remote_command = shlex.join([str(part) for part in command])
    completed = subprocess.run(
        [*ssh_base(), remote_command],
        input=input_data,
        capture_output=True,
        check=True,
        timeout=300,
    )
    stdout = completed.stdout
    return stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace")


def ssh_put(remote: str, data: bytes) -> None:
    """Upload bytes without interpolating *remote* into shell syntax."""
    if not remote.startswith("/") or "\x00" in remote:
        raise ValueError(f"Remote path must be an absolute POSIX path: {remote!r}")
    ssh_run(["tee", "--", remote], input_data=data)


def safe_leaf(value: object, *, suffix: str | None = None) -> str:
    """Validate an inbox-controlled filename component."""
    if not isinstance(value, str) or not _SAFE_LEAF.fullmatch(value):
        raise ValueError(f"Unsafe filename component: {value!r}")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError(f"Unsafe filename component: {value!r}")
    if suffix is not None and Path(value).suffix.lower() != suffix.lower():
        raise ValueError(f"Expected a {suffix} file, got {value!r}")
    return value


def normalize_photo(source: Path) -> bytes:
    """Return a normalized RGB JPEG; Pillow is loaded only for this operation."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - broken runtime installation
        raise RuntimeError(
            "Photo conversion needs the required Pillow dependency"
        ) from exc

    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Photo must be a regular file: {source}")
    size = source.stat().st_size
    if size <= 0 or size > MAX_PHOTO_BYTES:
        raise ValueError(
            f"Photo size must be between 1 and {MAX_PHOTO_BYTES} bytes"
        )

    output = io.BytesIO()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_PHOTO_PIXELS
                ):
                    raise ValueError(
                        f"Photo exceeds the {MAX_PHOTO_PIXELS}-pixel safety limit"
                    )
                image.convert("RGB").save(output, "JPEG", quality=92)
    except (
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError(f"Photo is not a valid bounded image: {source}") from exc
    result = output.getvalue()
    if not result or len(result) > MAX_PHOTO_BYTES:
        raise ValueError("Normalized JPEG exceeds the photo size safety limit")
    return result


def find_photo(nc: str, *, inbox: Path = INBOX) -> Path | None:
    """Find exactly one source photo for a validated catalogue code."""
    nc = safe_leaf(nc)
    matches = sorted(
        path for path in (Path(inbox) / "photos").glob(f"{nc}.*") if path.is_file()
    )
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise ValueError(f"Several photos found for {nc}: {names}")
    return matches[0] if matches else None


def upload_photo(
    nc: str,
    data: bytes,
    *,
    source_name: str = "",
    run: Callable[..., str] | None = None,
    put: Callable[[str, bytes], None] | None = None,
) -> str:
    """Upload one prevalidated photo and return its immutable public URL.

    The content hash is part of the public filename.  A failed publication can
    therefore leave only an unreferenced file; it can never replace the bytes
    behind a URL already used by the last-known-good feed.
    """
    nc = safe_leaf(nc)
    run = run or ssh_run
    put = put or ssh_put
    filename = photo_filename(nc, data)
    remote = f"{REMOTE_PHOTOS}/{filename}"
    temporary = f"{REMOTE_PHOTOS}/.{filename}.{uuid.uuid4().hex}.tmp"
    run(["mkdir", "-p", "--", REMOTE_PHOTOS])
    try:
        put(temporary, data)
        run(["chmod", "0644", "--", temporary])
        run(["mv", "-f", "--", temporary, remote])
    finally:
        try:
            run(["rm", "-f", "--", temporary])
        except (subprocess.SubprocessError, OSError):
            # A failed cleanup must not hide the upload/publication result.
            pass
    url = f"{PHOTO_BASE}/{filename}"
    print(f"  ↑ {source_name or nc} → {url}")
    return url


def photo_filename(nc: str, data: bytes) -> str:
    """Return the stable content-addressed filename for normalized JPEG bytes."""
    nc = safe_leaf(nc)
    digest = hashlib.sha256(data).hexdigest()
    return f"{nc}-{digest}.jpg"


def photo_public_url(nc: str, data: bytes) -> str:
    """Return the URL that :func:`upload_photo` will publish."""
    return f"{PHOTO_BASE}/{photo_filename(nc, data)}"


def validate_items(raw_items: object) -> list[dict[str, Any]]:
    """Validate untrusted YAML data before any local or remote mutation."""
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise ValueError("inbox.items must be a list")

    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"inbox item {index} must be a mapping")
        series_key = raw.get("series_key")
        if series_key is not None and (
            not isinstance(series_key, str) or not series_key.strip()
        ):
            raise ValueError(f"inbox item {index}: series_key must be non-empty text")
        series_key = series_key.strip() if isinstance(series_key, str) else None

        raw_photos = raw.get("photos") or []
        if not isinstance(raw_photos, list):
            raise ValueError(f"inbox item {index}: photos must be a list")
        photos = [safe_leaf(value) for value in raw_photos]

        description = raw.get("description")
        if description is not None:
            description = safe_leaf(description, suffix=".txt")

        force_nc = raw.get("force_nc")
        if force_nc is not None:
            force_nc = safe_leaf(force_nc)
        price = raw.get("price")
        if force_nc:
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                raise ValueError(f"inbox item {index}: forced product needs numeric price")
            if not math.isfinite(float(price)) or float(price) <= 0:
                raise ValueError(f"inbox item {index}: price must be finite and positive")

        series_name = raw.get("series_name")
        if series_name is not None and (
            not isinstance(series_name, str) or not series_name.strip()
        ):
            raise ValueError(f"inbox item {index}: series_name must be non-empty text")
        series_name = series_name.strip() if isinstance(series_name, str) else None

        if description and not (series_key or force_nc):
            raise ValueError(
                f"inbox item {index}: description needs series_key or force_nc"
            )
        items.append(
            {
                "series_key": series_key,
                "photos": photos,
                "description": description,
                "force_nc": force_nc,
                "price": price,
                "series_name": series_name,
            }
        )
    return items


def load_items(path: Path) -> list[dict[str, Any]]:
    """Load and validate an inbox YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError("inbox YAML root must be a mapping")
    return validate_items(data.get("items"))


def _section_bounds(lines: list[str], marker: str) -> tuple[int, int]:
    indexes = [index for index, line in enumerate(lines) if line == marker]
    if len(indexes) != 1:
        raise ValueError(f"Expected exactly one config marker: {marker.rstrip()!r}")
    start = indexes[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if indent <= 2:
            end = index
            break
    return start, end


def _mapping_key(line: str) -> str | None:
    if len(line) - len(line.lstrip(" ")) != 4 or line.lstrip().startswith("#"):
        return None
    try:
        loaded = yaml.safe_load(line.strip())
    except yaml.YAMLError:
        return None
    if isinstance(loaded, Mapping) and len(loaded) == 1:
        return str(next(iter(loaded)))
    return None


def _sequence_value(line: str) -> str | None:
    if len(line) - len(line.lstrip(" ")) != 4 or not line.lstrip().startswith("-"):
        return None
    try:
        loaded = yaml.safe_load(line.strip())
    except yaml.YAMLError:
        return None
    if isinstance(loaded, list) and len(loaded) == 1:
        return str(loaded[0])
    return None


def update_config_text(
    text: str,
    *,
    manual_photos: Mapping[str, str],
    force_include: Mapping[str, Mapping[str, Any]],
    selected_series: Sequence[str],
) -> str:
    """Update the three inbox-owned YAML sections while preserving comments."""
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, Mapping[str, str], Callable[[str], str | None]]] = []

    photo_lines = {
        key: f"    {json.dumps(key, ensure_ascii=False)}: "
        f"{json.dumps(value, ensure_ascii=False)}\n"
        for key, value in manual_photos.items()
    }
    force_lines: dict[str, str] = {}
    for key, value in force_include.items():
        fields = [f"price: {json.dumps(value['price'], ensure_ascii=False)}"]
        if value.get("series"):
            fields.append(
                f"series: {json.dumps(value['series'], ensure_ascii=False)}"
            )
        force_lines[key] = (
            f"    {json.dumps(key, ensure_ascii=False)}: "
            f"{{{', '.join(fields)}}}\n"
        )
    sections.extend(
        [
            ("  manual_photos:\n", photo_lines, _mapping_key),
            ("  force_include:\n", force_lines, _mapping_key),
        ]
    )

    for marker, replacements, key_reader in sections:
        start, end = _section_bounds(lines, marker)
        seen: set[str] = set()
        for index in range(start, end):
            key = key_reader(lines[index])
            if key in replacements:
                lines[index] = replacements[key]
                seen.add(key)
        new_lines = [line for key, line in replacements.items() if key not in seen]
        lines[start:start] = new_lines

    marker = "  selected_series:\n"
    start, end = _section_bounds(lines, marker)
    wanted = list(dict.fromkeys(selected_series))
    existing = {
        value for value in (_sequence_value(line) for line in lines[start:end]) if value
    }
    if wanted and "__none__" in existing:
        lines[start:end] = [
            line for line in lines[start:end] if _sequence_value(line) != "__none__"
        ]
        start, end = _section_bounds(lines, marker)
        existing.discard("__none__")
    additions = [
        f"    - {json.dumps(value, ensure_ascii=False)}\n"
        for value in wanted
        if value not in existing
    ]
    lines[start:start] = additions

    updated = "".join(lines)
    parsed = yaml.safe_load(updated)
    if not isinstance(parsed, Mapping):
        raise ValueError("Updated config is not a YAML mapping")
    catalog = parsed.get("catalog")
    if not isinstance(catalog, Mapping):
        raise ValueError("Updated config has no catalog mapping")
    for key, value in manual_photos.items():
        if (catalog.get("manual_photos") or {}).get(key) != value:
            raise ValueError(f"Failed to update manual photo for {key}")
    for key, value in force_include.items():
        actual = (catalog.get("force_include") or {}).get(key) or {}
        if actual.get("price") != value["price"] or actual.get("series") != value.get(
            "series"
        ):
            raise ValueError(f"Failed to update forced product {key}")
    selected = catalog.get("selected_series") or []
    if any(value not in selected for value in wanted):
        raise ValueError("Failed to update selected_series")
    return updated


def prepare_writes(
    items: Sequence[Mapping[str, Any]],
    photo_urls: Mapping[str, str],
    *,
    root: Path = ROOT,
    config_path: Path = CFG,
    manifest_path: Path = MANIFEST,
    inbox: Path = INBOX,
) -> dict[Path, bytes]:
    """Build every local file mutation in memory, without changing the disk."""
    root = Path(root).resolve()
    config_path = Path(config_path)
    manifest_path = Path(manifest_path)
    inbox = Path(inbox)
    config_text = config_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Description manifest must be a JSON object")

    force_include: dict[str, dict[str, Any]] = {}
    selected: list[str] = []
    writes: dict[Path, bytes] = {}
    for item in items:
        series_key = item.get("series_key")
        force_nc = item.get("force_nc")
        if force_nc:
            entry: dict[str, Any] = {"price": item["price"]}
            if item.get("series_name"):
                entry["series"] = item["series_name"]
            force_include[str(force_nc)] = entry
        elif series_key:
            selected.append(str(series_key))

        description = item.get("description")
        if description:
            description = safe_leaf(description, suffix=".txt")
            source = inbox / "descriptions" / description
            target = (root / "avito-descriptions" / description).resolve()
            target.relative_to(root)
            writes[target] = source.read_bytes()
            manifest_key = series_key or f"rusklimat|force|{force_nc}"
            manifest[str(manifest_key)] = description

    updated_config = update_config_text(
        config_text,
        manual_photos=photo_urls,
        force_include=force_include,
        selected_series=selected,
    )
    writes[config_path.resolve()] = updated_config.encode("utf-8")
    writes[manifest_path.resolve()] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return writes


def apply_transaction(
    writes: Mapping[Path, bytes], action: Callable[[], None] | None = None
) -> None:
    """Atomically write files and restore every original if any step fails."""
    originals = {
        Path(path): Path(path).read_bytes() if Path(path).exists() else None
        for path in writes
    }
    try:
        for path, data in writes.items():
            atomic_write(Path(path), data)
        if action is not None:
            action()
    except BaseException:
        rollback_errors: list[str] = []
        for path, original in reversed(list(originals.items())):
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original)
            except OSError as exc:
                rollback_errors.append(f"{path}: {exc}")
        if rollback_errors:
            raise RuntimeError(
                "Inbox failed and local rollback was incomplete: "
                + "; ".join(rollback_errors)
            )
        raise


def build_archive(
    *,
    root: Path = ROOT,
    description_keys: Sequence[str] = (),
) -> bytes:
    """Pack config plus only descriptions explicitly changed by this inbox."""
    root = Path(root)
    manifest_path = root / "avito-descriptions" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Description manifest must be a JSON object")
    upserts: dict[str, str] = {}
    description_data: dict[str, bytes] = {}
    for key in dict.fromkeys(description_keys):
        if not isinstance(key, str) or not key:
            raise ValueError("Description manifest keys must be non-empty strings")
        if key not in manifest:
            raise ValueError(f"Description is absent from manifest: {key!r}")
        raw_filename = manifest[key]
        filename = safe_leaf(raw_filename, suffix=".txt")
        path = root / "avito-descriptions" / filename
        data = path.read_bytes()
        if len(data) > 128 * 1024 or not data.decode("utf-8").strip():
            raise ValueError(f"Invalid description file: {filename!r}")
        upserts[key] = filename
        description_data[filename] = data
    patch_data = (
        json.dumps(
            {
                "schema_version": 1,
                "profile": "conditioners",
                "upserts": upserts,
                "deletions": [],
                "files_sha256": {
                    filename: hashlib.sha256(data).hexdigest()
                    for filename, data in description_data.items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        archive.add(
            root / "config" / "config.yaml",
            arcname="config/config.yaml",
        )
        patch_info = tarfile.TarInfo("description-patch/patch.json")
        patch_info.size = len(patch_data)
        patch_info.mode = 0o600
        archive.addfile(patch_info, io.BytesIO(patch_data))
        for filename, data in sorted(description_data.items()):
            info = tarfile.TarInfo(f"description-patch/files/{filename}")
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def deploy(archive_data: bytes) -> None:
    """Publish the staged profile through the remote fail-closed publisher."""
    remote_archive = f"{REMOTE_UPLOAD_DIR}/avito-inbox-{uuid.uuid4().hex}.tgz"
    ssh_run(["install", "-d", "-m", "700", "--", REMOTE_UPLOAD_DIR])
    ssh_put(remote_archive, archive_data)
    try:
        ssh_run(
            [
                "sh",
                "-eu",
                "-c",
                shlex.join(
                    [
                        "env",
                        f"PYTHONPATH={REMOTE_ROOT}/src",
                        f"{REMOTE_ROOT}/.venv/bin/python",
                        "-m",
                        "avito_bridge.profile_publish",
                        "--archive",
                        remote_archive,
                        "--config",
                        PROFILE_CONFIG,
                        "--bridge-root",
                        REMOTE_ROOT,
                        "--public-root",
                        REMOTE_PUBLIC_ROOT,
                    ]
                ),
            ]
        )
    finally:
        try:
            ssh_run(["rm", "-f", "--", remote_archive])
        except (subprocess.SubprocessError, OSError):
            # Do not mask the publication result merely because /tmp cleanup failed.
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the curated Avito inbox")
    parser.add_argument(
        "--inbox",
        default=str(INBOX / "inbox.yaml"),
        help="path to inbox YAML (default: inbox/inbox.yaml)",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="publish through the fail-closed remote publisher after local validation",
    )
    args = parser.parse_args(argv)

    items = load_items(Path(args.inbox))
    if not items:
        print(f"{args.inbox} пуст — нечего делать")
        return 0

    photo_data: dict[str, tuple[Path, bytes]] = {}
    for item in items:
        for nc in item["photos"]:
            if nc in photo_data:
                continue
            source = find_photo(nc, inbox=Path(args.inbox).parent)
            if source is None:
                raise FileNotFoundError(f"Фото не найдено: photos/{nc}.*")
            photo_data[nc] = (source, normalize_photo(source))

    photo_urls = {
        nc: photo_public_url(nc, data)
        for nc, (_source, data) in photo_data.items()
    }
    if args.deploy:
        for nc, (source, data) in photo_data.items():
            uploaded_url = upload_photo(nc, data, source_name=source.name)
            if uploaded_url != photo_urls[nc]:
                raise RuntimeError(
                    f"Непредсказуемый URL загруженного фото для {nc}: {uploaded_url}"
                )

    writes = prepare_writes(
        items,
        photo_urls,
        inbox=Path(args.inbox).parent,
    )

    def publish() -> None:
        changed_description_keys = [
            str(
                item.get("series_key")
                or f"rusklimat|force|{item.get('force_nc')}"
            )
            for item in items
            if item.get("description")
        ]
        deploy(build_archive(description_keys=changed_description_keys))

    apply_transaction(writes, publish if args.deploy else None)
    print("config.yaml / manifest.json / описания обновлены атомарно.")
    if args.deploy:
        print("Профиль проверен и опубликован через fail-closed publisher.")
    else:
        if photo_data:
            print(f"Подготовлено фотографий к загрузке: {len(photo_data)}.")
        print("Локальный режим: для безопасной публикации повторите с --deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
