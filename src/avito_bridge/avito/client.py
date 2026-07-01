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
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=30)
        self._token: str | None = None
        self._token_exp: float = 0

    def get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self.http.post(EP_TOKEN, data={"grant_type": "client_credentials",
                                           "client_id": self.client_id,
                                           "client_secret": self.client_secret})
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_exp = time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def get_self_id(self) -> int:
        r = self.http.get(EP_SELF, headers=self._auth())
        r.raise_for_status()
        return int(r.json()["id"])

    def get_last_report(self, uid: int) -> dict:
        r = self.http.get(ep_last_report(uid), headers=self._auth())
        r.raise_for_status()
        return r.json()

    def list_uploads(self) -> list[dict]:
        """GET /autoload/v4/uploads — список прогонов автозагрузки СО статистикой (не deprecated,
        в отличие от /autoload/v2/reports)."""
        r = self.http.get(EP_UPLOADS_V4, headers=self._auth())
        r.raise_for_status()
        return r.json().get("uploads", [])

    def last_successful_items(self) -> list[dict]:
        """GET /autoload/v4/uploads/last_successful/items — постатейный статус последней
        УСПЕШНОЙ загрузки: {ad_id, avito_id, avito_status, url, messages[]} на объявление."""
        r = self.http.get(f"{EP_UPLOADS_V4}/last_successful/items", headers=self._auth())
        r.raise_for_status()
        return r.json().get("items", [])


def status_by_ad_id(items: list[dict]) -> dict[str, dict]:
    """{ad_id: item} — по нашему ad_id (см. avito_bridge.feed.ad_id.make_ad_id) находим реальный
    статус объявления на Avito."""
    return {it["ad_id"]: it for it in items if it.get("ad_id")}
