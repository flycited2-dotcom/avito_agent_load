from __future__ import annotations
import time
import httpx

BASE_URL = "https://api.avito.ru"
EP_TOKEN = "/token"                                   # POST client_credentials
EP_SELF = "/core/v1/accounts/self"
EP_PROFILE = "/autoload/v2/profiles"                  # подтвердить по порталу (Фаза 0)
EP_UPLOAD = "/autoload/v1/upload"                     # подтвердить по порталу
EP_UPLOADS_V4 = "/autoload/v4/uploads"


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
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self._owns_http = http is None
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=30)
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = max(0.0, float(backoff_base))
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
            time.sleep(min(5.0, max(retry_after, self.backoff_base * (2 ** attempt))))
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

    def last_successful_items(self) -> list[dict]:
        """GET /autoload/v4/uploads/last_successful/items — постатейный статус последней
        УСПЕШНОЙ загрузки: {ad_id, avito_id, avito_status, url, messages[]} на объявление.

        Пагинация (подтверждено живым запросом 2026-07-02): perPage фиксирован сервером (=20,
        параметр per_page игнорируется), meta = {perPage, page, pages, total} — идём по page,
        пока не пройдём meta.pages. Без meta в ответе (старый/усечённый формат) — одна страница."""
        items: list[dict] = []
        page = 1
        while True:
            r = self._request(
                "GET",
                f"{EP_UPLOADS_V4}/last_successful/items",
                headers=self._auth(),
                params={"page": page},
            )
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("items", []))
            pages = min(1000, max(1, int((data.get("meta") or {}).get("pages") or 1)))
            if page >= pages:
                return items
            page += 1


def status_by_ad_id(items: list[dict]) -> dict[str, dict]:
    """{ad_id: item} — по нашему ad_id (см. avito_bridge.feed.ad_id.make_ad_id) находим реальный
    статус объявления на Avito."""
    return {it["ad_id"]: it for it in items if it.get("ad_id")}
