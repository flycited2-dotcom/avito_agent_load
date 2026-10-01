"""Read-only Avito status probe for visually held ready-price ads."""
from __future__ import annotations

import json
from pathlib import Path

from avito_bridge.avito.client import AvitoClient
from avito_bridge.ready_price.server_run import _credentials


ROOT = Path("/opt/avito-bridge")
TARGET_IDS = {
    "nikita-00-00000323", "nikita-00-00000369",
    "nikita-00-00001238", "nikita-00-00001381",
}


def main() -> None:
    ids = TARGET_IDS
    credentials = _credentials(ROOT)
    if not credentials:
        raise RuntimeError("Avito credentials unavailable")
    with AvitoClient(*credentials, pagination_delay=0.5) as client:
        uploads = client.list_uploads()
        report = client.last_successful_items(ids)
        avito_ids = {str(row.get("avito_id")): row["ad_id"] for row in report if row.get("avito_id")}
        live = [row for row in client.list_items() if str(row.get("id")) in avito_ids]
    print(json.dumps({
        "uploads": [{key: upload.get(key) for key in ("id", "status", "started_at", "finished_at", "created_at")}
                    for upload in uploads[:3]],
        "report": [{key: row.get(key) for key in ("ad_id", "avito_id", "avito_status", "url", "messages")} for row in report],
        "live": [{"ad_id": avito_ids[str(row["id"])], "avito_id": row["id"], "status": row.get("status")}
                 for row in live],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
