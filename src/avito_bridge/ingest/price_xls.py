"""Источник «price_xls»: опт-прайс поставщика БытТехОпт.

Схема листа подтверждена прайсом поставщика от 2026-07-16:
две строки шапки, затем колонки A=артикул, B=группа, C=бренд, D=наименование
(с префиксом-артикулом «003544 …»), E=цена (опт), F/G=наличие («Под заказ» —
в файле 2026-07-16 ВСЕ 1626 строк), H=заказ. Группы «Кондиционеры …» в фид
техники не берём — кондиционеры публикует профиль №1 из БД oasis.

Профиль задаёт (profile.source_options):
  path                 — путь к .xls или к актуальному .xlsx
  selected_groups      — whitelist групп прайса (пусто = ничего: курирование явное)
  group_tags           — общие XML-теги Avito по группе
  article_tags         — XML-теги конкретного артикула; перекрывают group_tags
  required_tags_by_group — обязательные теги группы; отсутствие останавливает сборку
  excluded_articles    — явно заблокированные до подготовки артикулы
  description_template — шаблон описания; поля {model} {brand} {group}
"""
from __future__ import annotations
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import xlrd
from openpyxl import load_workbook

from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

_ARTICLE_PREFIX_RE = re.compile(r"^\s*\d{4,}\s+")
_SECTION_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
_TOP_LEVEL_SECTION_RE = re.compile(r"^\s*\d+\.\s+")
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024
MAX_WORKBOOK_ROWS = 50_000

DEFAULT_DESCRIPTION = ("{model}\n\nНовый, в заводской упаковке, гарантия производителя. "
                       "Товар под заказ: срок поставки 1–3 дня. Симферополь, возможна доставка.")


def _clean_model(name: str) -> str:
    """«003544 Крышка CAPPELLO …, 24см,» → «Крышка CAPPELLO …, 24см»."""
    s = _ARTICLE_PREFIX_RE.sub("", str(name))
    s = re.sub(r"\s{2,}", " ", s).strip().rstrip(",;").strip()
    return s


def _as_positive_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
    else:
        text = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
    return number if number > 0 else None


def _clean_section_name(value: str) -> str:
    return _SECTION_PREFIX_RE.sub("", value).strip()


def _supplier_photo_files(rows: list[dict], source: Path, options: dict) -> dict[str, Path]:
    """Сопоставляет извлечённые из прайса миниатюры с товарными артикулами.

    Картинки нужны только как входные референсы контент-завода: в фид они не
    попадут, пока не будет сгенерирована уникальная карточка. Имя файла ASCII,
    чтобы одинаково работать на Windows и VPS.
    """
    directory_option = str(options.get("supplier_photo_dir", "supplier-thumbnails"))
    directory = Path(directory_option)
    if not directory.is_absolute():
        directory = source.parent / directory

    result: dict[str, Path] = {}
    for row in rows:
        article = str(row.get("article", ""))
        suffix = "".join(re.findall(r"\d+", article))
        if not suffix:
            continue
        filename = f"btopt-{suffix}.jpg"
        candidate = directory / filename
        if candidate.is_file():
            result[article] = candidate
    return result


def _supplier_photo_urls(files: dict[str, Path], options: dict) -> dict[str, str]:
    """Return public supplier URLs only when the profile explicitly provides one.

    A local image still becomes a content-factory reference via ``card_input_name``
    when the public shop is served from another host.
    """
    base_url = str(options.get("supplier_photo_base_url", "")).rstrip("/")
    if not base_url:
        return {}
    return {article: f"{base_url}/{quote(path.name)}" for article, path in files.items()}


def _parse_btopt_stock_xlsx(source: Path, options: dict) -> list[dict]:
    """Читает компактный прайс БытТехОпт с остатками.

    В письме от 28.07.2026 товар находится в колонке E, цены — N/O,
    остатки двух складов — P/Q. Группы заданы иерархическими строками, поэтому
    диапазон выбирается по заголовкам разделов, а не по неустойчивим номерам
    строк.
    """
    start_section = str(options.get("section_start", "")).strip()
    end_section = str(options.get("section_end", "")).strip()
    if not start_section or not end_section:
        raise ValueError(
            "price_xls: для layout=btopt_stock нужны section_start и section_end"
        )
    price_column = str(options.get("price_column", "purchase"))
    price_index = {"purchase": 13, "discount_100k": 14}.get(price_column)
    if price_index is None:
        raise ValueError(
            "price_xls: price_column должен быть purchase или discount_100k"
        )

    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        if sheet.max_row > MAX_WORKBOOK_ROWS:
            raise ValueError(
                "price_xls: слишком много строк "
                f"({sheet.max_row}, предел {MAX_WORKBOOK_ROWS})"
            )
        in_range = False
        after_end_section = False
        group = ""
        rows: list[dict] = []
        for values in sheet.iter_rows(values_only=True):
            values = tuple(values)
            name = str(values[4] or "").strip() if len(values) > 4 else ""
            if not name:
                continue
            if name == start_section:
                in_range = True
                group = _clean_section_name(name)
                continue
            if not in_range:
                continue
            if name == end_section:
                after_end_section = True
                group = _clean_section_name(name)
                continue
            if after_end_section and _TOP_LEVEL_SECTION_RE.match(name):
                break

            purchase = _as_positive_float(values[13] if len(values) > 13 else None)
            discount_100k = _as_positive_float(values[14] if len(values) > 14 else None)
            selected_price = _as_positive_float(
                values[price_index] if len(values) > price_index else None
            )
            if selected_price is None:
                group = _clean_section_name(name)
                continue

            main_stock = _as_positive_float(values[15] if len(values) > 15 else None) or 0
            management_stock = _as_positive_float(values[16] if len(values) > 16 else None) or 0
            article = str(values[0] or "").strip() if values else ""
            if not article:
                continue
            rows.append({
                "article": article,
                "group": group,
                "brand": "",
                "name": name,
                "price": selected_price,
                "purchase_price": purchase,
                "discount_100k_price": discount_100k,
                "stock": int(main_stock + management_stock),
                "stock_label": (
                    f"основной={int(main_stock)}; управленки={int(management_stock)}"
                ),
            })
        return rows
    finally:
        workbook.close()


def parse_price_xls(path: str | Path, options: dict | None = None) -> list[dict]:
    """Все товарные строки прайса (без фильтра групп): служебные строки шапки
    отсеиваются по нечисловой цене — как в transform.py excel-automation."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_WORKBOOK_BYTES:
        raise ValueError("price_xls: файл превышает безопасный предел 50 МБ")
    options = options or {}
    if source.suffix.lower() == ".xlsx":
        if options.get("layout") != "btopt_stock":
            raise ValueError(
                "price_xls: .xlsx поддерживается только с layout=btopt_stock"
            )
        return _parse_btopt_stock_xlsx(source, options)

    book = xlrd.open_workbook(str(source))
    try:
        sheet = book.sheet_by_index(0)
        if sheet.nrows > MAX_WORKBOOK_ROWS:
            raise ValueError(
                "price_xls: слишком много строк "
                f"({sheet.nrows}, предел {MAX_WORKBOOK_ROWS})"
            )
        rows = []
        for i in range(sheet.nrows):
            vals = sheet.row_values(i)
            name = str(vals[3]).strip() if len(vals) > 3 else ""
            price_raw = vals[4] if len(vals) > 4 else None
            if not name or not isinstance(price_raw, (int, float)) or price_raw <= 0:
                continue                               # шапка/заголовок/пустая строка
            rows.append({
                "article": str(vals[0]).strip(),
                "group": str(vals[1]).strip(),
                "brand": str(vals[2]).strip() if vals[2] else "",
                "name": name,
                "price": float(price_raw),
                "stock_label": (
                    str(vals[5]).strip()
                    if len(vals) > 5 and vals[5]
                    else ""
                ),
            })
        return rows
    finally:
        book.release_resources()


def build_offers(rows: list[dict], opts: dict,
                 manual_photos: dict | None = None,
                 manual_price_override: dict | None = None,
                 supplier_photos: dict | None = None,
                 supplier_card_inputs: dict | None = None) -> list[Offer]:
    selected = set(opts.get("selected_groups") or [])
    group_tags: dict = opts.get("group_tags") or {}
    article_tags: dict = opts.get("article_tags") or {}
    required_tags_by_group: dict = opts.get("required_tags_by_group") or {}
    excluded_articles = {str(value) for value in (opts.get("excluded_articles") or [])}
    template = opts.get("description_template") or DEFAULT_DESCRIPTION
    offers = []
    manual_photos = manual_photos or {}
    manual_price_override = manual_price_override or {}
    supplier_photos = supplier_photos or {}
    supplier_card_inputs = supplier_card_inputs or {}
    for r in rows:
        if r["group"] not in selected:
            continue
        article = str(r["article"])
        if article in excluded_articles:
            continue
        if opts.get("exclude_zero_stock", False) and r.get("stock", 1) <= 0:
            continue
        model = _clean_model(r["name"])
        attrs = {"group": r["group"],
                 "desc_long": template.format(model=model, brand=r["brand"], group=r["group"])}
        if supplier_card_inputs.get(r["article"]):
            attrs["card_input_name"] = str(supplier_card_inputs[r["article"]])
        tags = dict(group_tags.get(r["group"]) or {})
        tags.update(article_tags.get(article) or {})
        required_tags = {
            str(tag) for tag in (required_tags_by_group.get(r["group"]) or [])
        }
        missing_tags = sorted(
            tag for tag in required_tags
            if tag not in tags or not str(tags[tag]).strip()
        )
        if missing_tags:
            raise ValueError(
                "price_xls: для артикула "
                f"{article!r} из группы {r['group']!r} не заданы обязательные "
                f"теги Avito: {', '.join(missing_tags)}"
            )
        for tag, val in tags.items():
            attrs[f"avito_tag:{tag}"] = str(val)
        offers.append(Offer(
            supplier_sku=f"pricexls:{article}",
            source="price_xls",
            brand=r["brand"],
            model=model,
            category_id=None,
            cost=Decimal(str(r["price"])),
            stock=int(r.get("stock", 1)),
            photos=([manual_photos[r["article"]]]
                    if manual_photos.get(r["article"])
                    else ([supplier_photos[r["article"]]]
                          if supplier_photos.get(r["article"]) else [])),
            series=r["group"],             # группа прайса: наценка per-группа через pricing.rules
            attrs=attrs,
            price_override=(Decimal(str(manual_price_override[r["article"]]))
                            if manual_price_override.get(r["article"]) is not None else None),
        ))
    return offers


def fetch_price_xls(cfg: AppConfig) -> list[Offer]:
    opts = cfg.source_options or {}
    configured_path = opts.get("path", "")
    path = Path(configured_path).expanduser() if configured_path else None
    if path is not None and not path.is_absolute() and cfg.bridge_root:
        path = Path(cfg.bridge_root) / path
    if path is None or not path.exists():
        raise ValueError(f"price_xls: файл прайса не найден: '{configured_path}' — "
                         "укажи profile.source_options.path в профиле")
    rows = parse_price_xls(path, opts)
    supplier_files = _supplier_photo_files(rows, path, opts)
    return build_offers(
        rows, opts,
        manual_photos=cfg.catalog.manual_photos,
        manual_price_override=cfg.catalog.manual_price_override,
        supplier_photos=_supplier_photo_urls(supplier_files, opts),
        supplier_card_inputs={article: file.name for article, file in supplier_files.items()},
    )
