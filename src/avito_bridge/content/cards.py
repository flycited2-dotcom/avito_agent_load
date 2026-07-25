"""Card-aware подбор фото: если для товара есть СГЕНЕРИРОВАННАЯ уникальная карточка
(фотоагент кладёт её на сервер в папку как `{nc_code}.jpg`), используем её вместо
общего фото поставщика. Это снимает блок Avito «повторное размещение» по фото
(модели одной серии у поставщика делят одно фото).

Контракт с фотоагентом: имя файла = ключ товара (часть supplier_sku после ':',
т.е. nc_code/артикул), приведённый к безопасному виду (`card_key`). Папка и
публичный URL — в config (`cards`)."""
from __future__ import annotations
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from avito_bridge.models import Offer


MAX_CARD_BYTES = 25 * 1024 * 1024
MAX_CARD_PIXELS = 25_000_000
_FORMAT_EXTENSION = {"JPEG": ".jpg", "PNG": ".png"}
_ALLOWED_CARD_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


@dataclass
class CardConfig:
    enabled: bool = False
    dir: str = ""               # путь к папке с карточками на сервере
    base_url: str = ""          # публичный HTTPS-префикс этой папки
    exts: list = field(default_factory=lambda: [".jpg", ".jpeg", ".png"])
    require_for_publish: bool = False   # публиковать серию ТОЛЬКО при наличии уникальной карточки
    supplier_photo_series: frozenset = frozenset()   # серии на фото поставщика (мульти, без генер-карточки)
    max_images: int = 10        # максимум картинок в объявлении (лимит Avito)


def card_image_extension(
    path: Path, *, require_matching_suffix: bool = True
) -> str:
    """Fully decode a bounded JPEG/PNG and return its canonical extension.

    Merely checking a filename or Pillow's initial header is not enough for
    photo-agent output: a truncated image can pass ``Image.open`` and fail only
    while its pixel data is decoded.  Symlinks are rejected because cards are
    files managed by Bridge, not references to arbitrary server files.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Generated card is not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_CARD_BYTES:
        raise ValueError(
            f"Generated card size must be between 1 and {MAX_CARD_BYTES} bytes: {path}"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = (image.format or "").upper()
                if image_format not in _FORMAT_EXTENSION:
                    raise ValueError(
                        f"Generated card must be JPEG or PNG, got {image_format or 'unknown'}"
                    )
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_CARD_PIXELS:
                    raise ValueError(
                        "Generated card dimensions exceed the "
                        f"{MAX_CARD_PIXELS}-pixel safety limit"
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated generated cards are not supported")
                image.verify()

            # ``verify`` checks container integrity without decoding pixels.
            # Reopen and load to catch truncated/corrupt compressed image data.
            with Image.open(path) as image:
                if (image.format or "").upper() != image_format:
                    raise ValueError("Generated card format changed while validating")
                image.load()
    except (
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError(f"Generated card is not a valid JPEG/PNG: {path}") from exc

    extension = _FORMAT_EXTENSION[image_format]
    if require_matching_suffix:
        suffix = path.suffix.lower()
        allowed_suffixes = {".jpg", ".jpeg"} if extension == ".jpg" else {".png"}
        if suffix not in allowed_suffixes:
            raise ValueError(
                f"Generated card extension {suffix!r} does not match {image_format}"
            )
    return extension


def has_card(offer: Offer, cfg: CardConfig) -> bool:
    """Есть ли для товара сгенерированная уникальная карточка на сервере."""
    if not (cfg.enabled and cfg.dir):
        return False
    return existing_card_path(offer.supplier_sku, Path(cfg.dir), cfg.exts) is not None


def card_input_photo(offer) -> str | None:
    """Фото-вход для генерации карточки — кадр ВНУТРЕННЕГО блока (он «герой» карточки).
    У daichi фото[0] — монтаж (внутренний+пульт+крупный наружный): наружный доминирует, и GPT
    мельчит внутренний блок. Фото[1] у daichi — чистый внутренний блок → берём его.
    У breeze/rusklimat фото[0] уже с внутренним блоком."""
    photos = list(getattr(offer, "photos", []) or [])
    if not photos:
        return None
    if offer.source == "daichi" and len(photos) >= 2:
        return photos[1]
    return photos[0]


def card_key(supplier_sku: str) -> str:
    """Collision-safe key which retains the supplier/source namespace."""
    raw = supplier_sku.strip().replace(":", "__", 1)
    sanitized = re.sub(r"[\\/\s]+", "_", raw)
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]+", "_", sanitized).strip("._") or "card"


def legacy_card_key(supplier_sku: str) -> str:
    """Pre-0.3 key, retained only to read already generated card files/jobs."""
    raw = supplier_sku.split(":", 1)[-1].strip()
    return re.sub(r"[\\/\s]+", "_", raw)


def existing_card_path(
    supplier_sku: str, cards_dir: Path, extensions: list[str]
) -> Path | None:
    """Prefer a namespaced, fully decoded card and read valid legacy files."""
    for key in dict.fromkeys((card_key(supplier_sku), legacy_card_key(supplier_sku))):
        for extension in extensions:
            if not isinstance(extension, str):
                continue
            extension = extension.lower()
            if extension not in _ALLOWED_CARD_SUFFIXES:
                continue
            candidate = Path(cards_dir) / f"{key}{extension}"
            try:
                card_image_extension(candidate)
            except (OSError, ValueError):
                continue
            else:
                return candidate
    return None


def resolve_photos(offer: Offer, cfg: CardConfig) -> list[str]:
    """URL фото для объявления: сгенерированная карточка (если есть) — иначе фото поставщика.
    Если карточка найдена — возвращаем ТОЛЬКО её (чтобы не тащить общее фото-дубль серии).
    URL процент-кодируется (имя файла может быть кириллическим)."""
    if cfg.enabled and cfg.dir:
        card = existing_card_path(offer.supplier_sku, Path(cfg.dir), cfg.exts)
        if card is not None:
            # Nanoseconds avoid stale URLs when two replacements happen in one second.
            version = card.stat().st_mtime_ns
            url = (
                f"{cfg.base_url.rstrip('/')}/{quote(card.name)}?v={version}"
            )
            return [url]
    return list(offer.photos)[: cfg.max_images]      # фото поставщика (несколько), кап по лимиту Avito
