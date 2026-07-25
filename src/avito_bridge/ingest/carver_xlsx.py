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
BRIDGE_ROOT = Path(__file__).resolve().parents[3]
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024

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


def resolve_source_path(path: str | Path, bridge_root: Path | None = None) -> Path:
    """Resolve a profile path from the bridge checkout, not the launch directory."""
    source = Path(path).expanduser()
    if not source.is_absolute():
        source = Path(bridge_root) / source if bridge_root else BRIDGE_ROOT / source
    return source.resolve()


def _sheet(book):
    return book[SHEET_NAME] if SHEET_NAME in book.sheetnames else book.active


def _validated_workbook_path(path: str | Path) -> Path:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_WORKBOOK_BYTES:
        raise ValueError("carver_xlsx: файл превышает безопасный предел 50 МБ")
    return source


def parse_carver_xlsx(path: str | Path) -> list[dict]:
    """Читает товарные строки, сохраняя Excel-строку для точной привязки фото."""
    source = _validated_workbook_path(path)
    book = load_workbook(str(source), data_only=True, read_only=False)
    try:
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
    finally:
        book.close()


def extract_embedded_photos(path: str | Path) -> dict[str, bytes]:
    """Возвращает {article: image_bytes}; дубли/фото вне товарных строк — ошибка.

    openpyxl хранит якоря с нулевой индексацией. Колонка намеренно не участвует:
    в исходном файле часть широких JPEG визуально начинается в C, но относится к
    той же товарной строке.
    """
    source = _validated_workbook_path(path)
    book = load_workbook(str(source), data_only=True, read_only=False)
    try:
        sheet = _sheet(book)
        article_by_row = {
            row["row"]: row["article"] for row in parse_carver_xlsx(source)
        }
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
    finally:
        book.close()


def _generator_avito_tags(row: dict) -> dict[str, str]:
    # Avito validates these select fields against its generator catalog.  Some
    # CARVER names in that catalog use legacy suffixes, mixed case or Cyrillic
    # look-alike letters, so the supplier spelling cannot always be sent as-is.
    catalog_tags = {
        "PPG-1900IS": ("PPG-1900IS", "220 В", "1.8", "2.0"),
        "PPG-2000IS": ("PPG-2000IS", "220 В", "1.8", "2.0"),
        "PPG-3100I": ("PPG-3100I", "220 В", "2.2", "2.5"),
        "PPG-3600I": ("PPG-3600I", "220 В", "2.8", "3.0"),
        "PPG-3900": ("PPG-3900", "220 В", "2.9", "3.2"),
        "PPG-4000IS": ("PPG-4000IS", "220 В", "3.0", "3.5"),
        "PPG-5100I": ("PPG-5100i", "220 В", "4.2", "4.5"),
        "PPG-5100ISE": ("PPG-5100iSE", "220 В", "4.0", "4.5"),
        "PPG-6500": ("PPG-6500", "220 В", "5.0", "5.5"),
        "PPG-6500AM": ("PPG-6500АM", "220 В", "5.0", "5.5"),
        "PPG-6500E": ("PPG-6500e", "220 В", "5.0", "5.5"),
        "PPG-6500R": ("PPG-6500R", "220 В / 380 В", "5.0", "5.5"),
        "PPG-6600ISR": ("PPG-6600ISE", "220 В", "5.0", "5.5"),
        "PPG-8100I": ("PPG-8100I", "220 В", "6.0", "6.5"),
        "PPG-9000E": ("PPG-9000е", "220 В", "7.0", "7.5"),
        "PPG-9000R": ("PPG-9000е", "220 В", "7.0", "7.5"),
        "PPG-9500IR": ("PPG-9500IE", "220 В", "7.5", "8.0"),
        "PPG-10000E": ("PPG-10000е", "220 В", "8.0", "8.3"),
        "PPG-10000EM": ("PPG-10000ЕM", "220 В", "7.0", "7.5"),
        "PPG-10000R": ("PPG-10000е", "220 В", "8.0", "8.3"),
        "PPG-13500VR": ("PPG-13500EV", "220 В / 380 В", "8.1", "10.0"),
        "PPG-15000IR": ("PPG-15000IE", "220 В", "10.0", "11.0"),
        "PPG-15000IVR": ("PPG-15000iEV", "220 В / 380 В", "10.0", "11.0"),
    }
    model = str(row.get("model") or "").strip()
    if model in catalog_tags:
        avito_model, voltage, rated_power, maximum_power = catalog_tags[model]
        return {
            "Brand": "CARVER",
            "Model": avito_model,
            "FuelType": "Бензин",
            "Voltage": voltage,
            "RatedPower": rated_power,
            "MaximumPower": maximum_power,
        }

    characteristics = str(row.get("characteristics") or "")
    lines = characteristics.splitlines()

    def power_value(kind: str) -> str:
        values: list[str] = []
        for line in lines:
            lower = line.lower()
            if (kind not in lower or "мощност" not in lower or "квт" not in lower
                    or "двигател" in lower):
                continue
            tail = line.split(":", 1)[1] if ":" in line else ""
            numbers = re.findall(r"[0-9]+(?:[,.][0-9]+)?", tail)
            if not numbers:
                continue
            index = 1 if kind == "макс" and "номин" in lower and len(numbers) > 1 else 0
            values.append(numbers[index].replace(",", "."))
        return max(values, key=Decimal) if values else ""

    fuel = ""
    for line in lines:
        if "топливо" not in line.lower():
            continue
        lower = line.lower()
        if "бенз" in lower or "аи " in lower or "аи9" in lower:
            fuel = "Бензин"
        elif "диз" in lower:
            fuel = "Дизель"
        if fuel:
            break

    voltage = ""
    for line in lines:
        lower = line.lower()
        if "выход" not in lower or "напряжен" not in lower:
            continue
        has_230 = bool(re.search(r"(?<!\d)(?:220|230)(?!\d)", line))
        has_400 = bool(re.search(r"(?<!\d)(?:380|400)(?!\d)", line))
        if has_230 and has_400:
            voltage = "220/380 В"
        elif has_230:
            voltage = "220 В"
        break
    if not voltage:
        has_230 = bool(re.search(r"(?<!\d)(?:220|230)(?!\d)", characteristics))
        has_400 = bool(re.search(r"(?<!\d)(?:380|400)(?!\d)", characteristics))
        if has_230 and has_400:
            voltage = "220/380 В"
        elif has_230:
            voltage = "220 В"

    tags = {
        "Brand": "CARVER",
        "Model": str(row.get("model") or "").strip(),
        "FuelType": fuel,
        "Voltage": voltage,
        "RatedPower": power_value("номин"),
        "MaximumPower": power_value("макс"),
    }
    return {tag: value for tag, value in tags.items() if value}


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
        if row["kind"] == "generator":
            for tag, value in _generator_avito_tags(row).items():
                attrs[f"avito_tag:{tag}"] = value
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
    configured_path = opts.get("path", "")
    path = resolve_source_path(configured_path, cfg.bridge_root) if configured_path else None
    if path is None or not path.exists():
        raise ValueError(f"carver_xlsx: файл прайса не найден: '{configured_path}'")
    return build_offers(
        parse_carver_xlsx(path), opts,
        manual_photos=cfg.catalog.manual_photos,
        manual_price_override=cfg.catalog.manual_price_override,
    )
