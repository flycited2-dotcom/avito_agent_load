"""Read-only аудит действующего сервера перед следующим этапом источника Никиты.

Запускать на сервере его Python-окружением. Секреты читаются только server-side;
в stdout — краткий результат. Принудительный запуск автозагрузки отсутствует.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from decouple import Config, RepositoryEnv
from defusedxml import ElementTree as ET

from avito_bridge.avito.client import AvitoClient
from avito_bridge.profile_publish import _write_atomic_bytes


def save(path, value):
    _write_atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def audit(bridge: Path, public: Path, output: Path, *, account_catalog=False):
    output.mkdir(parents=True, exist_ok=True)
    feed = (public / "avito-feed.xml").read_bytes()
    root = ET.fromstring(feed)
    if root.tag != "Ads":
        raise ValueError("Invalid feed root")
    ads = root.findall("Ad")
    manifest = json.loads((bridge / "profiles/telegram-price-nikita.json").read_text(encoding="utf-8"))
    ids = {str(p["ad_id"]) for p in manifest["products"]}
    stops = json.loads((bridge / "state/manual-stop-main.json").read_text(encoding="utf-8"))
    if not isinstance(stops.get("entries"), dict):
        raise ValueError("Invalid manual stops")
    # Копии являются доказательствами времени проверки, никогда не используются как откат фида.
    _write_atomic_bytes(output / "current-feed.xml", feed)
    save(output / "manual-stop.json", stops)
    save(output / "published-manifest.json", manifest)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "feed_ads": len(ads),
              "feed_sha256": hashlib.sha256(feed).hexdigest(), "manual_stops": len(stops["entries"]),
              "first_batch_in_feed": [a.findtext("Id") for a in ads if a.findtext("Id") in ids],
              "forced_upload_started": False, "account_catalog_complete": False}
    credentials = None
    for path in [bridge / ".env", Path("/opt/content-factory/.env"), Path("/opt/oasis/.env")]:
        if path.is_file():
            cfg = Config(RepositoryEnv(str(path)))
            if cfg("AVITO_CLIENT_ID", default="") and cfg("AVITO_CLIENT_SECRET", default=""):
                credentials = cfg
                break
    if credentials is None:
        result["api_error"] = "configured_credentials_not_found"
    else:
        try:
            with AvitoClient(credentials("AVITO_CLIENT_ID"), credentials("AVITO_CLIENT_SECRET"), pagination_delay=1.1) as client:
                uploads = client.list_uploads()
                items = client.last_successful_items(ids)
                response = client._request("GET", "/autoload/v2/profile", headers=client._auth())
                response.raise_for_status()
                profile = response.json()
                result.update(autoload_enabled=profile.get("autoload_enabled"),
                              allow_pay_over_limit=profile.get("allow_pay_over_limit"),
                              main_feed_configured=any(f.get("feed_url") == "https://splithome.ru/static/avito-feed.xml"
                                                       for f in profile.get("feeds_data", [])),
                              uploads=[{k: x.get(k) for k in ["upload_id", "status", "started_at"]} for x in uploads[:3]],
                              items=items, pending_ids=sorted(ids - {str(x.get("ad_id")) for x in items}))
                # Отчёт автозагрузки и текущее состояние площадки — разные свидетельства.
                if account_catalog:
                    account = client.list_items()
                    save(output / "account-items.json", {"checked_at": datetime.now(timezone.utc).isoformat(),
                                                         "items": account})
                    result["account_catalog_complete"] = True
                    result["account_item_count"] = len(account)
                    by_id = {str(x.get("id")): x for x in account}
                    result["current_first_batch"] = [{"ad_id": x["ad_id"], "avito_id": x.get("avito_id"),
                                                      "status": by_id.get(str(x.get("avito_id")), {}).get("status", "unknown")}
                                                     for x in items]
        except Exception as exc:
            result["api_error"] = type(exc).__name__
    save(output / "avito-receipt.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, default=Path("/opt/avito-bridge"))
    parser.add_argument("--public", type=Path, default=Path("/opt/oasis/staticfiles"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-catalog", action="store_true")
    args = parser.parse_args()
    report = audit(args.bridge, args.public, args.output, account_catalog=args.account_catalog)
    print(json.dumps({k: v for k, v in report.items() if k not in {"items", "uploads"}}, ensure_ascii=True))
    raise SystemExit(1 if report.get("api_error") else 0)
