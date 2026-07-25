"""Источник «price_xls»: опт-прайс поставщика БытТехОпт (.xls, ежедневно
скачивается конвейером excel-automation в input/priceopt_YYYYMMDD.xls).

Схема листа подтверждена прайсом поставщика от 2026-07-16:
две строки шапки, затем колонки A=артикул, B=группа, C=бренд, D=наименование
(с префиксом-артикулом «003544 …»), E=цена (опт), F/G=наличие («Под заказ» —
в файле 2026-07-16 ВСЕ 1626 строк), H=заказ. Группы «Кондиционеры …» в фид
техники не берём — кондиционеры публикует профиль №1 из БД oasis.

Профиль задаёт (profile.source_options):
  path                 — путь к .xls
  selected_groups      — whitelist групп прайса (пусто = ничего: курирование явное)
  group_tags           — {группа: {GoodsType: …, GoodsSubType: …}} → attrs avito_tag:*
                         (feed/builder.py кладёт такие attrs отдельными XML-тегами)
  description_template — шаблон описания; поля {model} {brand} {group}
"""
from __future__ import annotations
import re
from decimal import Decimal
from pathlib import Path

import xlrd

from avito_bridge.config import AppConfig
from avito_bridge.models import Offer

_ARTICLE_PREFIX_RE = re.compile(r"^\s*\d{4,}\s+")
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024
MAX_WORKBOOK_ROWS = 50_000

DEFAULT_DESCRIPTION = ("{model}\n\nНовый, в заводской упаковке, гарантия производителя. "
                       "Товар под заказ: срок поставки 1–3 дня. Симферополь, возможна доставка.")


def _clean_model(name: str) -> str:
    """«003544 Крышка CAPPELLO …, 24см,» → «Крышка CAPPELLO …, 24см»."""
    s = _ARTICLE_PREFIX_RE.sub("", str(name))
    s = re.sub(r"\s{2,}", " ", s).strip().rstrip(",;").strip()
    return s


def parse_price_xls(path: str | Path) -> list[dict]:
    """Все товарные строки прайса (без фильтра групп): служебные строки шапки
    отсеиваются по нечисловой цене — как в transform.py excel-automation."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > MAX_WORKBOOK_BYTES:
        raise ValueError("price_xls: файл превышает безопасный предел 50 МБ")
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
                 manual_price_override: dict | None = None) -> list[Offer]:
    selected = set(opts.get("selected_groups") or [])
    group_tags: dict = opts.get("group_tags") or {}
    template = opts.get("description_template") or DEFAULT_DESCRIPTION
    offers = []
    manual_photos = manual_photos or {}
    manual_price_override = manual_price_override or {}
    for r in rows:
        if r["group"] not in selected:
            continue
        model = _clean_model(r["name"])
        attrs = {"group": r["group"],
                 "desc_long": template.format(model=model, brand=r["brand"], group=r["group"])}
        for tag, val in (group_tags.get(r["group"]) or {}).items():
            attrs[f"avito_tag:{tag}"] = str(val)
        offers.append(Offer(
            supplier_sku=f"pricexls:{r['article']}",
            source="price_xls",
            brand=r["brand"],
            model=model,
            category_id=None,
            cost=Decimal(str(r["price"])),
            stock=1,                       # весь прайс «Под заказ» — публикуем осознанно
            photos=([manual_photos[r["article"]]]
                    if manual_photos.get(r["article"]) else []),
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
    return build_offers(
        parse_price_xls(path), opts,
        manual_photos=cfg.catalog.manual_photos,
        manual_price_override=cfg.catalog.manual_price_override,
    )
