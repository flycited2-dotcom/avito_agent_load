"""Persist manual Avito removals so later XML uploads cannot republish them."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from avito_bridge.avito.client import AvitoClient
from avito_bridge.feed.writer import write_atomic


def _read_json(path: Path, default: dict) -> dict:
    path = Path(path)
    if not path.is_file():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _write_json_atomic(path: Path, value: dict) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_atomic(text, Path(path))


def load_suppressed_ids(path: Path) -> set[str]:
    """Load immutable manual exclusions. Malformed state aborts a feed build."""
    data = _read_json(Path(path), {"version": 1, "entries": {}})
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        raise ValueError(f"Invalid manual stop entries in {path}")
    return {str(ad_id) for ad_id in entries}


def _feed_items(path: Path) -> dict[str, str]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.parse(str(path), parser).getroot()
    if root.tag != "Ads":
        raise ValueError(f"Invalid Avito feed root in {path}: {root.tag!r}")
    result: dict[str, str] = {}
    for ad in root.findall("Ad"):
        ad_id = (ad.findtext("Id") or "").strip()
        if ad_id:
            result[ad_id] = (ad.findtext("Title") or "").strip()
    return result


def expired_listing(mapped: dict, now: str) -> bool:
    if (mapped.get("section") or {}).get("slug") == "stopped_by_expiration":
        return True
    try:
        end = datetime.fromisoformat(str(mapped["avito_date_end"]).replace("Z", "+00:00"))
        return end.tzinfo is not None and end <= datetime.fromisoformat(now)
    except (KeyError, TypeError, ValueError):
        return False


def sync_manual_stops(
    client: AvitoClient,
    feed_path: Path,
    stop_path: Path,
    observation_path: Path,
    *,
    bootstrap_removed: bool = False,
    hold_archive: bool = False,
) -> dict[str, int]:
    """
    Observe live Avito statuses and remember only explicit ``active -> removed``
    transitions. Moderation states and ordinary expiry never suppress a product.

    ``bootstrap_removed`` is an explicit one-time import for listings the owner
    already removed before the observer was installed.
    """
    managed = _feed_items(feed_path)
    upload_items = client.last_successful_items(ad_ids=managed)
    live_items = client.list_items()
    live_by_id = {
        str(item.get("id")): item
        for item in live_items
        if item.get("id") is not None
    }

    previous_doc = _read_json(
        observation_path, {"version": 1, "observations": {}}
    )
    previous = previous_doc.get("observations", {})
    if not isinstance(previous, dict):
        raise ValueError(f"Invalid observations in {observation_path}")

    stop_doc = _read_json(stop_path, {"version": 1, "entries": {}})
    entries = stop_doc.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError(f"Invalid manual stop entries in {stop_path}")

    now = datetime.now(timezone.utc).isoformat()
    current = dict(previous)
    added = 0
    observed = 0
    mapped_by_ad = {
        ad_id: item for ad_id, item in previous.items() if ad_id in managed
    }
    mapped_by_ad.update({
        str(item.get("ad_id")): item
        for item in upload_items
        if str(item.get("ad_id") or "") in managed
    })
    for ad_id, mapped in mapped_by_ad.items():
        avito_id_raw = mapped.get("avito_id")
        avito_id = str(avito_id_raw) if avito_id_raw is not None else ""
        live = live_by_id.get(avito_id, {})
        # An upload report is historical, not evidence of the current status.
        status = str(live.get("status") or "").lower()
        if not status:
            continue
        observed += 1
        old_status = str((previous.get(ad_id) or {}).get("status") or "").lower()
        current[ad_id] = {
            "ad_id": ad_id,
            "avito_id": int(avito_id) if avito_id.isdigit() else avito_id,
            "status": status,
            "title": managed[ad_id],
            "observed_at": now,
            "avito_date_end": mapped.get("avito_date_end"),
        }
        removal_transition = old_status == "active" and (
            status == "removed" or (hold_archive and status == "old")
        )
        # Explicit one-time bootstrap also imports removals seen by an earlier
        # observation-only seed run. Existing stop entries remain idempotent.
        bootstrap_removal = bootstrap_removed and status == "removed"
        if (removal_transition or bootstrap_removal) and ad_id not in entries:
            entries[ad_id] = {
                "ad_id": ad_id,
                "avito_id": int(avito_id) if avito_id.isdigit() else avito_id,
                "status": status,
                "reason": ("expired_avito_listing" if expired_listing(mapped, now) else "archive_hold")
                          if status == "old" else "manual_avito_removal",
                "title": managed[ad_id],
                "created_at": now,
            }
            added += 1

    _write_json_atomic(
        stop_path,
        {"version": 1, "updated_at": now, "entries": entries},
    )
    # Persist the decision before advancing its observation cursor.
    _write_json_atomic(
        observation_path,
        {"version": 1, "updated_at": now, "observations": current},
    )
    return {
        "managed": len(managed),
        "observed": observed,
        "added": added,
        "suppressed": len(entries),
    }


def suppress_feed(source: Path, target: Path, stop_path: Path) -> tuple[int, int]:
    """Remove stopped ads from a complete merged feed and write it atomically."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.parse(str(source), parser).getroot()
    if root.tag != "Ads":
        raise ValueError("Expected Ads root")
    stopped = load_suppressed_ids(stop_path)
    removed = 0
    for ad in list(root.findall("Ad")):
        if (ad.findtext("Id") or "").strip() in stopped:
            root.remove(ad)
            removed += 1
    remaining = len(root.findall("Ad"))
    if not remaining:
        raise ValueError("Manual stop filter would make the Avito feed empty")
    if not removed and Path(source).resolve() == Path(target).resolve():
        return 0, remaining
    document = etree.tostring(root, encoding="unicode", pretty_print=True)
    write_atomic('<?xml version="1.0" encoding="UTF-8"?>\n' + document, target)
    return removed, remaining


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remember manual Avito removals as persistent feed exclusions"
    )
    parser.add_argument("--feed", required=True, type=Path)
    parser.add_argument("--stop", default="state/manual-stop-main.json", type=Path)
    parser.add_argument(
        "--observations", default="state/avito-status-main.json", type=Path
    )
    parser.add_argument("--bootstrap-removed", action="store_true")
    parser.add_argument("--apply-feed", action="store_true")
    parser.add_argument("--hold-archive", action="store_true", help="Hold active-to-old transitions pending owner review, including expiry")
    args = parser.parse_args(argv)
    client_id = os.getenv("AVITO_CLIENT_ID") or os.getenv("AVITO_MAIN_CLIENT_ID")
    client_secret = os.getenv("AVITO_CLIENT_SECRET") or os.getenv("AVITO_MAIN_CLIENT_SECRET")
    if not client_id or not client_secret:
        parser.error(
            "AVITO_CLIENT_ID/AVITO_CLIENT_SECRET (or AVITO_MAIN_*) are required"
        )
    # Large accounts need multiple pages. A small inter-page delay and longer
    # retry window keep us below Avito's burst limit without dropping state.
    with AvitoClient(
        client_id,
        client_secret,
        max_retries=6,
        backoff_base=1.0,
        pagination_delay=0.35,
    ) as client:
        expected = os.getenv("AVITO_EXPECTED_ACCOUNT_ID")
        if expected and client.get_self_id() != int(expected):
            raise ValueError("Avito account mismatch")
        result = sync_manual_stops(
            client,
            args.feed,
            args.stop,
            args.observations,
            bootstrap_removed=args.bootstrap_removed,
            hold_archive=args.hold_archive,
        )
    if args.apply_feed and set(_feed_items(args.feed)) & load_suppressed_ids(args.stop):
        backups = args.stop.parent / "manual-stop-backups"
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.feed, backups / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".xml"))
        removed, remaining = suppress_feed(args.feed, args.feed, args.stop)
        result.update(feed_removed=removed, feed_remaining=remaining)
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
