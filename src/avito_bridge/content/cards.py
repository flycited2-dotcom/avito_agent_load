"""Card-aware подбор фото: если для товара есть СГЕНЕРИРОВАННАЯ уникальная карточка
(фотоагент кладёт её на сервер в папку как `{nc_code}.jpg`), используем её вместо
общего фото поставщика. Это снимает блок Avito «повторное размещение» по фото
(модели одной серии у поставщика делят одно фото).

Контракт с фотоагентом: имя файла = ключ товара (часть supplier_sku после ':',
т.е. nc_code/артикул), приведённый к безопасному виду (`card_key`). Папка и
публичный URL — в config (`cards`)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from avito_bridge.models import Offer


@dataclass
class CardConfig:
    enabled: bool = False
    dir: str = ""               # путь к папке с карточками на сервере
    base_url: str = ""          # публичный HTTPS-префикс этой папки
    exts: list = field(default_factory=lambda: [".jpg", ".jpeg", ".png"])


def card_key(supplier_sku: str) -> str:
    """Ключ файла карточки = id-часть артикула (после ':'), безопасная для ФС/URL."""
    raw = supplier_sku.split(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw.strip())


def resolve_photos(offer: Offer, cfg: CardConfig) -> list[str]:
    """URL фото для объявления: сгенерированная карточка (если есть) — иначе фото поставщика.
    Если карточка найдена — возвращаем ТОЛЬКО её (чтобы не тащить общее фото-дубль серии)."""
    if cfg.enabled and cfg.dir:
        key = card_key(offer.supplier_sku)
        for ext in cfg.exts:
            if (Path(cfg.dir) / f"{key}{ext}").exists():
                return [f"{cfg.base_url.rstrip('/')}/{key}{ext}"]
    return list(offer.photos)
