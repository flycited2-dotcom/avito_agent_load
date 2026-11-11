from __future__ import annotations
import time
import httpx

BASE_URL = "https://api.avito.ru"
EP_TOKEN = "/token"                                   # POST client_credentials
EP_SELF = "/core/v1/accounts/self"
EP_PROFILE = "/autoload/v2/profiles"                  # подтвердить по порталу (Фаза 0)
EP_UPLOAD = "/autoload/v1/upload"                     # подтвердить по порталу
EP_UPLOADS_V4 = "/autoload/v4/uploads"
EP_ITEMS = "/core/v1/items"


def ep_last_report(uid):
    return f"/autoload/v1/accounts/{uid}/reports/last_report/"


def parse_report(rep: dict) -> tuple[set[str], dict[str, list[str]]]:
    published: set[str] = set()
    rejected: dict[str, list[str]] = {}
    for it in rep.get("items", []):
        ad_id = it.get("ad_id")
        if it.get("status") == "published":
            published.add(ad_id)
        elif it.get("status") == "rejected":
            rejected[ad_id] = it.get("messages", [])
    return published, rejected


class AvitoClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: httpx.Client | None = None,
        *,
        max_retries: int = 2,
        backoff_base: float = 0.25,
        pagination_delay: float = 0.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self._owns_http = http is None
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=30)
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = max(0.0, float(backoff_base))
        self.pagination_delay = max(0.0, float(pagination_delay))
        self._token: str | None = None
        self._token_exp: float = 0

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> "AvitoClient":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        retry_statuses = {429, 500, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self.http.request(method, path, **kwargs)
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
            else:
                if response.status_code not in retry_statuses or attempt >= self.max_retries:
                    return response
                response.close()
            retry_after = 0.0
            if response is not None:
                try:
                    retry_after = float(response.headers.get("retry-after", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
            # Avito commonly answers 429 with a Retry-After close to one minute.
            # Respect it; a five-second cap only repeats the same rejected page.
            time.sleep(min(60.0, max(retry_after, self.backoff_base * (2 ** attempt))))
        raise RuntimeError("unreachable")  # pragma: no cover

    def get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self._request(
            "POST",
            EP_TOKEN,
            data={"grant_type": "client_credentials",
                  "client_id": self.client_id,
                  "client_secret": self.client_secret},
        )
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def get_self_id(self) -> int:
        r = self._request("GET", EP_SELF, headers=self._auth())
        r.raise_for_status()
        return int(r.json()["id"])

    def get_last_report(self, uid: int) -> dict:
        r = self._request("GET", ep_last_report(uid), headers=self._auth())
        r.raise_for_status()
        return r.json()

    def list_uploads(self) -> list[dict]:
        """GET /autoload/v4/uploads — список прогонов автозагрузки СО статистикой (не deprecated,
        в отличие от /autoload/v2/reports)."""
        r = self._request("GET", EP_UPLOADS_V4, headers=self._auth())
        r.raise_for_status()
        return r.json().get("uploads", [])

    def last_successful_items(self, ad_ids=None) -> list[dict]:
        """GET /autoload/v4/uploads/last_successful/items — постатейный статус последней
        УСПЕШНОЙ загрузки: {ad_id, avito_id, avito_status, url, messages[]} на объявление.

        Official pagination uses camel-case perPage, NOT per_page."""
        return self._upload_items("last_successful", ad_ids)

    def current_items(self, ad_ids) -> list[dict]:
        """Read scoped item receipts while the current upload is processing."""
        return self._upload_items("current", ad_ids)

    def _upload_items(self, upload: str, ad_ids=None) -> list[dict]:
        if ad_ids is not None:
            ids = sorted(set(str(value) for value in ad_ids))
            result = []
            # The account-wide report has unstable pagination (confirmed live).
            # Query explicit feed IDs in single-page batches instead.
            for start in range(0, len(ids), 50):
                chunk = ids[start:start + 50]
                r = self._request('GET', f'{EP_UPLOADS_V4}/{upload}/items',
                                  headers=self._auth(), params={'query': ','.join(chunk), 'page': 1, 'perPage': 100})
                r.raise_for_status()
                data = r.json()
                rows = data.get('items', [])
                meta = data.get('meta') or {}
                if int(meta.get('pages') or 1) > 1 or len(rows) != int(meta.get('total', len(rows))):
                    raise ValueError('Incomplete filtered autoload report')
                if any(str(row.get('ad_id')) not in chunk for row in rows):
                    raise ValueError('Autoload query returned unrelated IDs')
                result.extend(rows)
                if self.pagination_delay and start + 50 < len(ids):
                    time.sleep(self.pagination_delay)
            return result
        items: list[dict] = []
        page = 1
        while True:
            r = self._request(
                "GET",
                f"{EP_UPLOADS_V4}/{upload}/items",
                headers=self._auth(),
                params={"page": page, "perPage": 100},
            )
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("items", []))
            pages = min(1000, max(1, int((data.get("meta") or {}).get("pages") or 1)))
            if page >= pages:
                unique = {str(it.get("ad_id")): it for it in items if it.get("ad_id")}
                total = (data.get("meta") or {}).get("total")
                if total is not None and len(unique) != int(total):
                    raise ValueError("Incomplete autoload pagination; refusing partial snapshot")
                return list(unique.values())
            if self.pagination_delay:
                time.sleep(self.pagination_delay)
            page += 1

    def list_items(
        self,
        statuses: tuple[str, ...] = ("active", "removed", "old", "blocked", "rejected"),
    ) -> list[dict]:
        """Return the account's current Avito items, including manually removed ones."""
        resources: list[dict] = []
        per_page = 99
        # Separate status queries make coverage explicit; comma-separated statuses
        # are also supported by the official API.
        for status in statuses:
            page = 1
            while True:
                r = self._request(
                    "GET",
                    EP_ITEMS,
                    headers=self._auth(),
                    params={
                        "status": status,
                        "page": page,
                        "per_page": per_page,
                    },
                )
                r.raise_for_status()
                data = r.json()
                batch = data.get("resources", []) or []
                resources.extend(batch)
                meta = data.get("meta") or {}
                pages = meta.get("pages") or meta.get("page_count")
                if pages is not None and page >= min(1000, max(1, int(pages))):
                    break
                if pages is None and len(batch) < int(
                    meta.get("per_page") or meta.get("perPage") or per_page
                ):
                    break
                if not batch or page >= 1000:
                    break
                if self.pagination_delay:
                    time.sleep(self.pagination_delay)
                page += 1
            if self.pagination_delay and status != statuses[-1]:
                time.sleep(self.pagination_delay)
        return resources


def status_by_ad_id(items: list[dict]) -> dict[str, dict]:
    """{ad_id: item} — по нашему ad_id (см. avito_bridge.feed.ad_id.make_ad_id) находим реальный
    статус объявления на Avito."""
    return {it["ad_id"]: it for it in items if it.get("ad_id")}
