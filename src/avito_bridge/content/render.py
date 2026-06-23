from __future__ import annotations
import hashlib
from dataclasses import dataclass
from avito_bridge.models import Offer, Content
from avito_bridge.content.sizing import size_from_btu


@dataclass
class ContentConfig:
    title_max: int = 50
    description_max: int = 7000
    stop_words: list[str] = None


# Тип по категории каталога.
_TYPE_LABEL = {2: "Настенная сплит-система", 6: "Полупромышленный кондиционер",
               7: "Мобильный кондиционер"}
# Рекомендованная площадь по типоразмеру (стандартная отраслевая таблица, kBTU -> м²).
_AREA_BY_SIZE = {7: 20, 9: 25, 10: 28, 12: 35, 13: 38, 14: 40, 16: 45, 18: 50,
                 20: 55, 22: 60, 24: 70, 26: 75, 28: 80, 30: 85, 36: 100, 42: 120,
                 48: 140, 60: 170}
# ТТХ-строки, дублирующие заголовок/бренд — в описание не выводим.
_SKIP_SPECS = {"Бренд", "Модель", "Серия", "Модель внутреннего блока",
               "Модель наружного блока", "Эффективен для помещений площадью до"}


def _strip_stopwords(text: str, stop_words: list[str]) -> str:
    out = text
    for w in (stop_words or []):
        out = out.replace(w, "").replace(w.capitalize(), "")
    return out


def _seed(offer: Offer) -> int:
    """Стабильное число из артикула — для детерминированной вариативности текста."""
    return int(hashlib.sha1(offer.supplier_sku.encode("utf-8")).hexdigest(), 16)


def _pick(options: list[str], seed: int) -> str:
    return options[seed % len(options)]


def _is_inverter(offer: Offer) -> bool:
    return "инвертор" in f"{offer.model} {offer.series or ''}".lower()


def _title(offer: Offer) -> str:
    size = size_from_btu(offer.btu_calc, offer.category_id)
    bits = [b for b in [offer.brand, offer.model] if b]
    base = " ".join(bits)
    if size:
        base = f"{base} {size}000 BTU".strip()
    return base


def _headline(offer: Offer, size, area, seed) -> str:
    type_label = _TYPE_LABEL.get(offer.category_id, "Кондиционер")
    name = f"{offer.brand} {offer.model}".strip()
    nl = name.lower()
    if offer.category_id == 7:
        conveys = "мобильн" in nl or "кондиционер" in nl
    else:
        conveys = "сплит" in nl or "кондиционер" in nl
    lead = name if conveys else f"{type_label} {name}"   # не задваиваем тип, если он уже в названии
    if size and area:
        variants = [
            f"{lead}: производительность {size}000 BTU — для помещений до {area} м².",
            f"{lead} — {size}000 BTU, комфортная прохлада в комнатах до {area} м².",
            f"{lead}. Мощность {size}000 BTU рассчитана на площадь до {area} м².",
        ]
        return _pick(variants, seed)
    return f"{lead}."


def _benefit(offer: Offer, seed) -> str:
    if _is_inverter(offer):
        return ("Инверторный компрессор плавно держит заданную температуру: тихая работа "
                "и заметная экономия электроэнергии по сравнению с обычными моделями.")
    if offer.category_id == 7:
        return ("Мобильный формат без монтажа — достаточно вывести воздуховод в окно. "
                "Легко переносится между комнатами, готов к работе сразу из коробки.")
    return ("Быстрое охлаждение в жару и мягкий обогрев в межсезонье — "
            "равномерный комфортный микроклимат без сквозняков.")


_SEASON = [
    "Сезон в Крыму жаркий и короткий: в пик спроса монтажные бригады расписаны на недели "
    "вперёд. Бронируйте установку заранее — встретите жару в прохладе.",
    "Лето на полуострове не щадит: не ждите аншлага у монтажников — берите технику и "
    "записывайтесь на установку, пока есть свободные даты.",
]
_CTA = [
    "Напишите нам — подберём оптимальную модель под ваш метраж и бюджет, рассчитаем "
    "мощность и сориентируем по срокам монтажа.",
    "Поможем с выбором: подскажем модель под площадь и теплопритоки помещения, "
    "посчитаем мощность и подскажем по доставке и установке.",
]


def _spec_lines(attrs: dict) -> list[str]:
    out = []
    for k, v in attrs.items():
        if k in _SKIP_SPECS:
            continue
        v = (v or "").replace("( - )", "").replace("()", "").strip()
        if v:
            out.append(f"• {k}: {v}")
    return out


def _footer(seed: int) -> list[str]:
    return [
        "",
        "Почему берут у нас:",
        "— только новое, с официальной гарантией производителя;",
        "— доставка по всему Крыму;",
        "— профессиональный монтаж и пусконаладка опытными бригадами;",
        "— честный подбор по площади и теплопритокам помещения.",
        "",
        _pick(_SEASON, seed),
        "",
        _pick(_CTA, seed),
    ]


def _money(p: int) -> str:
    return f"{p:,}".replace(",", " ") + " ₽"


def render_series(group, prices: dict, cfg: ContentConfig) -> Content:
    """Описание ОДНОГО объявления на серию: заголовок + таблица «типоразмер → цена»
    (только в наличии) + продающий текст. `prices` = {supplier_sku члена: цена}.
    `group` — SeriesGroup (duck-typed: brand, series, category_id, members, representative)."""
    rep = group.representative
    seed = _seed(rep)
    type_label = _TYPE_LABEL.get(group.category_id, "Кондиционер")
    inv = _is_inverter(rep)

    title_base = f"{type_label} {group.brand} {group.series}".strip()
    if inv and "инвертор" not in title_base.lower():
        title_base += " инвертор"
    title = _strip_stopwords(title_base, cfg.stop_words)[: cfg.title_max].strip()

    rows = []
    sizes = []
    for m in group.members:
        p = prices.get(m.supplier_sku)
        if not p:
            continue
        size = size_from_btu(m.btu_calc, m.category_id)
        if size:
            sizes.append(size)
            area = _AREA_BY_SIZE.get(size)
            label = f"{size}000 BTU" + (f" (до {area} м²)" if area else "")
        else:
            label = m.model
        rows.append((size or 10 ** 6, f"• {label} — {_money(p)}"))
    rows.sort()

    if sizes:
        head = (f"{group.brand} {group.series}: {type_label.lower()}, "
                f"типоразмеры {min(sizes)}–{max(sizes)} тыс. BTU в наличии.")
    else:
        head = f"{group.brand} {group.series} — {type_label.lower()}."
    lines = [head, "", _benefit(rep, seed)]
    if rows:
        lines += ["", "Цены по типоразмерам (в наличии):"] + [r for _, r in rows]
    specs = _spec_lines(rep.attrs)
    if specs:
        lines += ["", "Характеристики:"] + specs
    lines += _footer(seed)
    desc = _strip_stopwords("\n".join(lines), cfg.stop_words)[: cfg.description_max].strip()
    return Content(title=title, description=desc, from_cache=False)


def render_content(offer: Offer, cfg: ContentConfig) -> Content:
    """Продающее описание из РЕАЛЬНЫХ данных (без выдуманных характеристик),
    с детерминированной вариативностью по артикулу. Слой написан вручную, без LLM."""
    seed = _seed(offer)
    size = size_from_btu(offer.btu_calc, offer.category_id)
    area = _AREA_BY_SIZE.get(size) if size else None

    title = _strip_stopwords(_title(offer), cfg.stop_words)[: cfg.title_max].strip()

    lines = [_headline(offer, size, area, seed), "", _benefit(offer, seed)]
    specs = _spec_lines(offer.attrs)
    if specs:                             # реальные ТТХ из каталога (если есть)
        lines += ["", "Характеристики:"] + specs
    lines += _footer(seed)
    desc = _strip_stopwords("\n".join(lines), cfg.stop_words)[: cfg.description_max].strip()
    return Content(title=title, description=desc, from_cache=False)
