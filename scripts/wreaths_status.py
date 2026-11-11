#!/usr/bin/env python3
"""Print Avito autoload status without doing network work during import.

Examples::

    python scripts/wreaths_status.py
    python scripts/wreaths_status.py --profile conditioners --limit 10
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from decouple import Config, RepositoryEnv, UndefinedValueError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from avito_bridge.avito.client import AvitoClient

ROOT = Path(__file__).resolve().parent.parent
_PROFILE_SUFFIXES = {
    "conditioners": "",
    "wreaths": "_WREATHS",
    "carver": "_CARVER",
    "appliances": "_APPLIANCES",
}


def credential_names(profile: str) -> tuple[str, str]:
    """Return the environment variable names owned by one business profile."""
    try:
        suffix = _PROFILE_SUFFIXES[profile]
    except KeyError as exc:
        raise ValueError(f"Unknown profile: {profile!r}") from exc
    return f"AVITO_CLIENT_ID{suffix}", f"AVITO_CLIENT_SECRET{suffix}"


def load_credentials(profile: str, env_file: Path) -> tuple[str, str]:
    """Load profile-specific credentials, preferring the process environment."""
    client_id_name, secret_name = credential_names(profile)
    repository = Config(RepositoryEnv(str(env_file))) if env_file.is_file() else None

    def read(name: str) -> str:
        value = os.environ.get(name)
        if value is None and repository is not None:
            try:
                value = repository(name)
            except UndefinedValueError:
                value = None
        if not value or not value.strip():
            raise ValueError(f"Credential {name} is not configured")
        return value.strip()

    return read(client_id_name), read(secret_name)


def print_status(
    client: AvitoClient,
    *,
    limit: int = 5,
    output: TextIO = sys.stdout,
) -> int:
    """Fetch and render uploads plus the latest successful item statuses."""
    print("=== Прогоны автозагрузки (свежие сверху) ===", file=output)
    uploads = client.list_uploads()
    for upload in uploads[:limit]:
        stats = upload.get("stats") or {}
        print(
            f"{upload.get('started_at', '?')}  id={upload.get('upload_id', '?')}  "
            f"status={upload.get('status', '?')}  обработано={stats.get('count')}",
            file=output,
        )
        for event in upload.get("events") or []:
            print(
                f"    [{event.get('type')}] {event.get('description')}",
                file=output,
            )

    print(file=output)
    print("=== Постатейно (последняя УСПЕШНАЯ загрузка) ===", file=output)
    items = client.last_successful_items()
    by_status = Counter(item.get("avito_status") for item in items)
    print(f"всего: {len(items)}, по статусам: {dict(by_status)}", file=output)
    for item in items:
        if item.get("avito_status") != "active" or item.get("messages"):
            print(
                f"  {item.get('ad_id')}: {item.get('avito_status')} "
                f"{item.get('messages') or ''}",
                file=output,
            )
    return len(items)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show profile-specific Avito status")
    parser.add_argument(
        "--profile",
        choices=sorted(_PROFILE_SUFFIXES),
        default="wreaths",
    )
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be non-negative")

    try:
        client_id, client_secret = load_credentials(
            args.profile, Path(args.env_file)
        )
        with AvitoClient(client_id, client_secret) as client:
            print_status(client, limit=args.limit)
    except Exception as exc:
        print(f"Ошибка статуса профиля {args.profile}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
