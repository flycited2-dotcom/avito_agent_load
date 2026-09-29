import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from decouple import Config, RepositoryEnv

from avito_bridge import profile_publish as pp
from avito_bridge.avito.client import AvitoClient
from .publish import _unresolved_batch_ids, publish_ready_content
from .visual_hold import apply_visual_holds
from .remote import run


_AUTOLOAD_GET = re.compile(
    r'\[([^]]+)\] "GET /static/avito-feed\.xml HTTP/[^\"]+" 200 (\d+) '
    r'"[^\"]*" "([^\"]+)"'
)


def _autoload_fetch_confirmed(upload: dict, access_log: Path,
                              expected_bytes: int) -> bool:
    """The current hourly upload has already downloaded our complete XML.

    Avito reports ``processing`` for most of the hour after its one HTTP GET.
    A verified fetch means a later atomic XML change belongs to the next hourly
    upload. Missing logs or any mismatch keep the previous fail-closed behavior.
    """
    if upload.get("status") != "processing":
        return False
    try:
        started = datetime.fromisoformat(upload["started_at"].replace("Z", "+00:00"))
        with access_log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 8_000_000))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            match = _AUTOLOAD_GET.search(line)
            if (not match or int(match.group(2)) != expected_bytes or
                    "Avitobot-Autoload" not in match.group(3)):
                continue
            fetched = datetime.strptime(match.group(1), "%d/%b/%Y:%H:%M:%S %z")
            if -10 <= (fetched - started).total_seconds() <= 120:
                return True
    except (OSError, KeyError, ValueError, TypeError):
        return False
    return False


def _credentials(bridge: Path):
    for path in (bridge / ".env", Path("/opt/content-factory/.env"), Path("/opt/oasis/.env")):
        if not path.is_file():
            continue
        cfg = Config(RepositoryEnv(str(path)))
        client_id = cfg("AVITO_CLIENT_ID", default="")
        client_secret = cfg("AVITO_CLIENT_SECRET", default="")
        if client_id and client_secret:
            return client_id, client_secret
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("/opt/avito-ready-price"))
    p.add_argument("--bridge", type=Path, default=Path("/opt/avito-bridge"))
    p.add_argument("--public", type=Path, default=Path("/opt/oasis/staticfiles"))
    p.add_argument("--autoload-access-log", type=Path,
                   default=Path("/var/log/nginx/access.log"))
    a = p.parse_args()
    status_file = a.bridge / "state/ready-price/last-run.json"
    try:
        result = run(a.root / "inbox", a.root / "archive",
                     a.bridge / "state/ready-price/catalog.sqlite",
                     a.bridge / "config/ready-price-categories.yaml",
                     a.bridge / "profiles/telegram-price-nikita.json",
                     a.public / "avito-feed.xml", a.bridge / "state/manual-stop-main.json",
                     a.bridge, a.public)
    except RuntimeError as exc:
        # Другой штатный издатель уже держит общую блокировку. Это не авария:
        # таймер повторит обработку через 10 минут, а входящий bundle останется на месте.
        if "publication is already running" not in str(exc):
            raise
        status = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "deferred",
            "reason": "publication_lock_busy",
        }
        pp._write_atomic_bytes(
            status_file,
            json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        print(json.dumps({**status, "status_file": str(status_file)}, ensure_ascii=True))
        return
    with pp._exclusive_lock(a.bridge / "state/profile-publish.lock"):
        visual_holds = apply_visual_holds(
            a.public / "avito-feed.xml",
            a.bridge / "state/ready-price/visual-holds.json",
        )
    content = {"status": "credentials_unavailable", "added": [], "held": {}}
    credentials = _credentials(a.bridge)
    if (a.bridge / "state/ready-price/content-publication.paused").exists():
        content = {"status": "paused_visual_audit", "added": [], "held": {}}
    elif credentials:
        try:
            with AvitoClient(*credentials, pagination_delay=1.1) as client:
                uploads = client.list_uploads()
                latest = uploads[0] if uploads else {}
                latest_upload = latest.get("status", "")
                fetched = _autoload_fetch_confirmed(
                    latest, a.autoload_access_log,
                    (a.public / "avito-feed.xml").stat().st_size)
                if latest_upload == "processing" and not fetched:
                    # No proof that Avito has finished downloading the XML.
                    report_items, account_items = [], []
                else:
                    unresolved_ids = _unresolved_batch_ids(
                        a.bridge / "state/ready-price/catalog.sqlite")
                    report_items = client.last_successful_items(unresolved_ids) if unresolved_ids else []
                    account_items = client.list_items()
            with pp._exclusive_lock(a.bridge / "state/profile-publish.lock"):
                content = publish_ready_content(
                    a.bridge / "state/ready-price/catalog.sqlite",
                    a.public / "avito-feed.xml",
                    a.bridge / "state/manual-stop-main.json",
                    a.bridge,
                    a.public,
                    a.root / "content",
                    a.bridge / "state/ready-price/avito-schema/schemas.json",
                    account_items,
                    report_items,
                    latest_upload_status=("fetched_for_current_upload" if fetched
                                          else latest_upload),
                )
                if fetched:
                    content["upload_guard"] = "current_autoload_fetch_confirmed"
        except RuntimeError as exc:
            if "publication is already running" not in str(exc):
                raise
            content = {"status": "deferred_lock_busy", "added": [], "held": {}}
    status = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **result,
        "visual_holds": visual_holds,
        "content": content,
    }
    pp._write_atomic_bytes(
        status_file,
        json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    update = result["update"]
    print(json.dumps({
        "processed": len(result["processed"]),
        "import_errors": sum(item.get("status") == "error" for item in result["processed"]),
        "feed_status": update["status"],
        "updated": len(update["updated"]),
        "removed": len(update["removed"]),
        "skipped": len(update["skipped"]),
        "latest_sha256": update["latest_sha256"],
        "content_status": content["status"],
        "content_added": len(content.get("added", [])),
        "content_held": len(content.get("held", {})),
        "status_file": str(status_file),
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
