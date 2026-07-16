from __future__ import annotations
import hashlib


def make_ad_id(supplier_sku: str, city_id: str, revision: int = 0) -> str:
    """Детерминированный стабильный Avito Id для (оффер × город).

    revision — «перевыпуск» объявления, которое модерация отклонила несправедливо:
    новый Id = для Avito новое объявление = свежая проверка (старое уходит из фида
    и закрывается). revision=0 даёт ИСТОРИЧЕСКИЙ id — живые объявления стабильны."""
    raw = f"{supplier_sku}|{city_id}" + (f"|r{revision}" if revision else "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
