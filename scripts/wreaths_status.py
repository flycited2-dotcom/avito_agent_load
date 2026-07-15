"""Статус Автозагрузки 2-го аккаунта (венки): прогоны v4 uploads + постатейный разбор.

Запуск из корня avito-bridge (нужны AVITO_CLIENT_ID_WREATHS / AVITO_CLIENT_SECRET_WREATHS в .env):
    ..\\avito-studio\\.venv\\Scripts\\python -X utf8 scripts\\wreaths_status.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from decouple import Config, RepositoryEnv
from avito_bridge.avito.client import AvitoClient

cfg = Config(RepositoryEnv(str(Path(__file__).parent.parent / ".env")))
client = AvitoClient(cfg("AVITO_CLIENT_ID_WREATHS"), cfg("AVITO_CLIENT_SECRET_WREATHS"))

print("=== Прогоны автозагрузки (свежие сверху) ===")
for u in client.list_uploads()[:5]:
    stats = u.get("stats") or {}
    print(f"{u['started_at']}  id={u['upload_id']}  status={u['status']}  "
          f"обработано={stats.get('count')}")
    for e in u.get("events") or []:
        print(f"    [{e.get('type')}] {e.get('description')}")

print()
print("=== Постатейно (последняя УСПЕШНАЯ загрузка) ===")
try:
    items = client.last_successful_items()
except Exception as e:
    print(f"нет успешных загрузок: {e}")
    sys.exit(0)
by_status = Counter(it.get("avito_status") for it in items)
print(f"всего: {len(items)}, по статусам: {dict(by_status)}")
for it in items:
    if it.get("avito_status") != "active" or it.get("messages"):
        print(f"  {it.get('ad_id')}: {it.get('avito_status')} {it.get('messages') or ''}")
