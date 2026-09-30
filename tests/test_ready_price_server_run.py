from pathlib import Path
from datetime import datetime, timezone

from avito_bridge.ready_price.server_run import _autoload_fetch_confirmed, autoload_health


def test_old_successful_upload_does_not_confirm_current_feed():
    now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    upload = {"upload_id": 42, "status": "success_warning", "started_at": "2026-09-28T19:50:03Z"}
    assert autoload_health(upload, current=now)["status"] == "stale"
    assert autoload_health({**upload, "started_at": now.isoformat()}, current=now)["status"] == "recent"
    assert autoload_health({}, current=now)["status"] == "unknown"
    assert autoload_health({"started_at": "2026-09-30T12:00:00"}, current=now)["status"] == "unknown"


def test_processing_upload_is_safe_after_matching_avito_get(tmp_path: Path):
    log = tmp_path / "access.log"
    log.write_text(
        '176.114.123.108 - - [22/Sep/2026:22:23:04 +0300] '
        '"GET /static/avito-feed.xml HTTP/1.1" 200 450688 "-" '
        '"Mozilla/5.0 (compatible; Avitobot-Autoload;+http://autoload.avito.ru/format/)"\n',
        encoding="utf-8",
    )
    upload = {"status": "processing", "started_at": "2026-09-22T19:23:04Z"}
    assert _autoload_fetch_confirmed(upload, log, 450688)
    assert not _autoload_fetch_confirmed(upload, log, 450689)
    assert not _autoload_fetch_confirmed(
        {**upload, "started_at": "2026-09-22T20:23:04Z"}, log, 450688)
    assert not _autoload_fetch_confirmed(upload, tmp_path / "missing.log", 450688)


def test_untrusted_request_does_not_unlock_processing_upload(tmp_path: Path):
    log = tmp_path / "access.log"
    log.write_text(
        '127.0.0.1 - - [22/Sep/2026:22:23:04 +0300] '
        '"GET /static/avito-feed.xml HTTP/1.1" 200 450688 "-" "Mozilla/5.0"\n',
        encoding="utf-8",
    )
    assert not _autoload_fetch_confirmed(
        {"status": "processing", "started_at": "2026-09-22T19:23:04Z"}, log, 450688)
