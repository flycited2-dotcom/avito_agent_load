"""Источник прайса CARVER с фотографиями внутри XLSX.

Ожидаемая схема листа «Прайс склада»: заголовки в строке 3, далее
B=модель, C=наименование, D=встроенное фото, E=характеристики,
F=закупочная цена. Фотография сопоставляется только по номеру строки —
никакого внешнего или нечёткого поиска.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

SHEET_NAME = "Прайс склада"
FIRST_DATA_ROW = 4

DEFAULT_DESCRIPTION = (
    "{name}\n\n{characteristics}\n\n"
    "Новый товар CARVER. Симферополь: самовывоз или доставка по Крыму."
)


def sku_for_model(model: str) -> str:
    """Стабильный и безопасный артикул для XML, YAML и имени фото на VPS."""
    value = str(model or "").strip().upper().replace("А", "A").replace("М", "M")
    value = re.sub(r"[^A-Z0-9]+", "-", value).strip("-")
    if not value:
        raise ValueError("carver_xlsx: пустая или недопустимая модель")
    return value


def _sheet(book):
    return book[SHEET_NAME] if SHEET_NAME in book.sheetnames else book.active


def parse_carver_xlsx(path: str | Path) -> list[dict]:
    """Читает товарные строки, сохраняя Excel-строку для точной привязки фото."""
    book = load_workbook(str(path), data_only=True, read_only=False)
    sheet = _sheet(book)
    rows: list[dict] = []
    for row_number in range(FIRST_DATA_ROW, sheet.max_row + 1):
        model = str(sheet.cell(row_number, 2).value or "").strip()
        name = str(sheet.cell(row_number, 3).value or "").strip()
        price_raw = sheet.cell(row_number, 6).value
        if not model or not name or not isinstance(price_raw, (int, float)) or price_raw <= 0:
            continue
        rows.append({
            "row": row_number,
            "article": sku_for_model(model),
            "model": model,
            "name": name,
            "characteristics": str(sheet.cell(row_number, 5).value or "").strip(),
            "price": float(price_raw),
            "kind": "ats" if model.upper().startswith("ATS") else "generator",
        })
    return rows


def extract_embedded_photos(path: str | Path) -> dict[str, bytes]:
    """Возвращает {article: image_bytes}; дубли/фото вне товарных строк — ошибка.

    openpyxl хранит якоря с нулевой индексацией. Колонка намеренно не участвует:
    в исходном файле часть широких JPEG визуально начинается в C, но относится к
    той же товарной строке.
    """
    book = load_workbook(str(path), data_only=True, read_only=False)
    sheet = _sheet(book)
    article_by_row = {r["row"]: r["article"] for r in parse_carver_xlsx(path)}
    photos: dict[str, bytes] = {}
    for image in sheet._images:
        anchor = getattr(image, "anchor", None)
        start = getattr(anchor, "_from", None)
        if start is None:
            continue
        article = article_by_row.get(start.row + 1)
        if not article:
            continue
        if article in photos:
            raise ValueError(f"carver_xlsx: больше одного фото для {article}")
        photos[article] = image._data()
    return photos


def build_offers(rows: list[dict], opts: dict,
                 manual_photos: dict | None = None,
                 manual_price_override: dict | None = None) -> list[Offer]:
    template = opts.get("description_template") or DEFAULT_DESCRIPTION
    tags_by_kind: dict = opts.get("tags_by_kind") or {}
    manual_photos = manual_photos or {}
    manual_price_override = manual_price_override or {}
    offers: list[Offer] = []
    for row in rows:
        article = row["article"]
        attrs = {
            "kind": row["kind"],
            "model_code": row["model"],
            "desc_long": template.format(**row),
        }
        for tag, value in (tags_by_kind.get(row["kind"]) or {}).items():
            attrs[f"avito_tag:{tag}"] = str(value)
        offers.append(Offer(
            supplier_sku=f"carver:{article}",
            source="carver_xlsx",
            brand="CARVER",
            model=row["name"],
            category_id=None,
            cost=Decimal(str(row["price"])),
            stock=1,
            photos=([manual_photos[article]] if manual_photos.get(article) else []),
            series="Автоматика ATS" if row["kind"] == "ats" else "Генераторы CARVER",
            attrs=attrs,
            price_override=(Decimal(str(manual_price_override[article]))
                            if manual_price_override.get(article) is not None else None),
        ))
    return offers


def fetch_carver_xlsx(cfg: AppConfig) -> list[Offer]:
    opts = cfg.source_options or {}
    path = opts.get("path", "")
    if not path or not Path(path).exists():
        raise ValueError(f"carver_xlsx: файл прайса не найден: '{path}'")
    return build_offers(
        parse_carver_xlsx(path), opts,
        manual_photos=cfg.catalog.manual_photos,
        manual_price_override=cfg.catalog.manual_price_override,
    )
