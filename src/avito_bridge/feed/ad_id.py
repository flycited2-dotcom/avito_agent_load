from __future__ import annotations
import hashlib


def make_ad_id(supplier_sku: str, city_id: str) -> str:
    """Детерминированный стабильный Avito Id для (оффер × город)."""
    raw = f"{supplier_sku}|{city_id}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:24]
