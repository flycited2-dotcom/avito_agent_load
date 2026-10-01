"""Cache the official Avito autoload taxonomy used by ready-price products.

The command is read-only with respect to Avito.  It stores the raw tree, every
leaf schema below ``Бытовая техника`` and the television schema so feed
generation can validate values against the same version of the official docs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from decouple import Config, RepositoryEnv

from avito_bridge.avito.client import AvitoClient
from avito_bridge.profile_publish import _write_atomic_bytes


HOUSEHOLD_PATH = ("Для дома и дачи", "Бытовая техника")
TELEVISION_PATH = ("Электроника", "Аудио и видео", "Телевизоры и проекторы", "Телевизоры")


def _walk(nodes: list[dict], path: tuple[str, ...] = ()):
    for node in nodes:
        current = path + (str(node.get("name") or ""),)
        yield node, current
        yield from _walk(node.get("nested") or [], current)


def selected_leaves(tree: dict) -> list[tuple[str, tuple[str, ...]]]:
    leaves: dict[str, tuple[str, ...]] = {}
    for node, path in _walk(tree.get("categories") or []):
        nested = node.get("nested") or []
        slug = str(node.get("slug") or "")
        in_household = path[: len(HOUSEHOLD_PATH)] == HOUSEHOLD_PATH
        if slug and not nested and (in_household or path == TELEVISION_PATH):
            leaves[slug] = path
    return sorted(leaves.items())


def _save(path: Path, value: object) -> None:
    _write_atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def cache(output: Path, env_file: Path, *, delay: float = 0.25) -> dict:
    cfg = Config(RepositoryEnv(str(env_file)))
    output.mkdir(parents=True, exist_ok=True)
    with AvitoClient(
        cfg("AVITO_CLIENT_ID"),
        cfg("AVITO_CLIENT_SECRET"),
        max_retries=4,
        backoff_base=1.0,
    ) as client:
        response = client._request("GET", "/autoload/v1/user-docs/tree", headers=client._auth())
        response.raise_for_status()
        tree = response.json()
        leaves = selected_leaves(tree)
        schemas: dict[str, dict] = {}
        for index, (slug, path) in enumerate(leaves):
            response = client._request(
                "GET", f"/autoload/v1/user-docs/node/{slug}/fields", headers=client._auth()
            )
            response.raise_for_status()
            schemas[slug] = {"path": list(path), "schema": response.json()}
            if delay and index + 1 < len(leaves):
                time.sleep(delay)

    checked_at = datetime.now(timezone.utc).isoformat()
    _save(output / "tree.json", tree)
    _save(output / "schemas.json", {"checked_at": checked_at, "leaves": schemas})
    summary = {
        "checked_at": checked_at,
        "leaf_count": len(schemas),
        "slugs": sorted(schemas),
    }
    _save(output / "last-run.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("state/ready-price/avito-schema"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    print(json.dumps(cache(args.output, args.env_file, delay=max(0.0, args.delay))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
