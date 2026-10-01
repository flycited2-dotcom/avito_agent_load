"""Read-only capability audit for the main Avito account.

The script deliberately avoids message sends, listing mutations, paid services,
price/stock changes and order transitions. It prints only aggregate counts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from avito_bridge.avito.client import AvitoClient


def _count(data: Any, *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        value = data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int):
            return value
    return None


def _first_item_id(state_path: Path) -> int | None:
    if not state_path.is_file():
        return None
    data = json.loads(state_path.read_text(encoding="utf-8"))
    for value in (data.get("observations") or {}).values():
        item_id = value.get("avito_id")
        if isinstance(item_id, int) or str(item_id).isdigit():
            return int(item_id)
    return None


def main() -> int:
    client_id = os.environ["AVITO_CLIENT_ID"]
    client_secret = os.environ["AVITO_CLIENT_SECRET"]
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    date_from = start.date().isoformat()
    date_to = now.date().isoformat()
    dt_from = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    dt_to = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    item_id = _first_item_id(Path("state/avito-status-main.json"))

    with AvitoClient(
        client_id,
        client_secret,
        max_retries=4,
        backoff_base=1.0,
        pagination_delay=0.35,
    ) as client:
        user_id = client.get_self_id()

        probes: list[tuple[str, str, str, dict, Callable[[Any], int | None]]] = [
            ("items", "GET", "/core/v1/items",
             {"params": {"status": "active", "page": 1, "per_page": 1}},
             lambda d: _count(d, ("meta", "total"), ("resources",))),
            ("autoload", "GET", "/autoload/v4/uploads",
             {}, lambda d: _count(d, ("uploads",))),
            ("autoload_profile", "GET", "/autoload/v2/profile",
             {}, lambda _d: None),
            ("chats", "GET", f"/messenger/v2/accounts/{user_id}/chats",
             {"params": {"limit": 1, "offset": 0}},
             lambda d: _count(d, ("chats",))),
            ("rating", "GET", "/ratings/v1/info",
             {}, lambda _d: None),
            ("reviews", "GET", "/ratings/v1/reviews",
             {"params": {"limit": 1, "offset": 0}},
             lambda d: _count(d, ("reviews",), ("result", "reviews"))),
            ("tariff", "GET", "/tariff/info/1",
             {}, lambda _d: None),
            ("balance", "GET", f"/core/v1/accounts/{user_id}/balance/",
             {}, lambda _d: None),
            ("analytics", "POST", f"/stats/v2/accounts/{user_id}/items",
             {"json": {"dateFrom": date_from, "dateTo": date_to,
                       "metrics": ["views", "contacts"], "grouping": "totals",
                       "limit": 10, "offset": 0}},
             lambda d: _count(d, ("result",), ("data",))),
            ("spendings", "POST", f"/stats/v2/accounts/{user_id}/spendings",
             {"json": {"dateFrom": date_from, "dateTo": date_to,
                       "spendingTypes": ["all"], "grouping": "day"}},
             lambda d: _count(d, ("result",), ("data",))),
            ("operations", "POST", "/core/v1/accounts/operations_history/",
             {"json": {"dateTimeFrom": dt_from, "dateTimeTo": dt_to}},
             lambda d: _count(d, ("result", "operations"))),
            ("calls", "POST", "/calltracking/v1/getCalls/",
             {"json": {"dateTimeFrom": dt_from, "dateTimeTo": dt_to,
                       "limit": 1, "offset": 0}},
             lambda d: _count(d, ("calls",), ("result", "calls"))),
            ("orders", "GET", "/order-management/1/orders",
             {"params": {"page": 1, "limit": 1}},
             lambda d: _count(d, ("orders",), ("result", "orders"))),
            ("hierarchy", "GET", "/checkAhUserV2",
             {}, lambda _d: None),
        ]
        if item_id is not None:
            probes.extend([
                ("stock", "POST", "/stock-management/1/info",
                 {"json": {"item_ids": [item_id], "strong_consistency": False}},
                 lambda d: _count(d, ("stocks",), ("result",))),
                ("promotion_info", "POST", "/promotion/v1/items/services/get",
                 {"json": {"itemIds": [item_id]}},
                 lambda d: _count(d, ("result",), ("services",))),
            ])

        print(f"account_id={user_id} probe_item={'yes' if item_id else 'no'}")
        headers = client._auth()
        for name, method, path, kwargs, summarize in probes:
            try:
                response = client._request(method, path, headers=headers, **kwargs)
                status = response.status_code
                count = None
                if 200 <= status < 300:
                    try:
                        count = summarize(response.json())
                    except (ValueError, TypeError):
                        count = None
                suffix = f" count={count}" if count is not None else ""
                print(f"{name}: status={status}{suffix}")
            except Exception as exc:  # network-only audit; keep subsequent probes running
                print(f"{name}: error={type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
