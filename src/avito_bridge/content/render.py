from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from avito_bridge.models import Offer, Content
from avito_bridge.content.sizing import size_from_btu


@dataclass
class ContentConfig:
    title_max: int = 50
    description_max: int = 7000
    stop_words: list[str] = None
    website_link: str = ""              # текст-ссылка в футер (напр. «Каталог: splithome.ru»)
    website_link_keys: frozenset = frozenset()   # серии (key), к которым добавляем ссылку (тест → одна)
    descriptions: dict = None           # {series_key: готовый текст описания} — переопределяет генерацию


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


# Акронимы/токены, которые НЕ трогаем (иначе испортим). Модель-коды и версии ловим по цифрам/дефису.
_KEEP_UPPER = {"DC", "EU", "AC", "LG", "MDV", "LED", "USB", "BTU", "II", "III", "IV", "HD", "UV"}


def _smart_title(s: str) -> str:
    """Длинные КАПС-слова → Капс (Avito принудительно делает строчными КАПС длиннее ~3 букв:
    «FUNAI SENSEI» → на Avito «funai sensei»). Акронимы (DC/EU/LG), модель-коды и версии (R32, 2.0,
    N6, Wi-Fi) сохраняем. Title-Case-слова (Midea, Hisense, Inverter) не трогаем."""
    out = []
    for w in s.split():
        if any(ch.isdigit() for ch in w) or "-" in w or "/" in w:
            out.append(w)                              # модель-коды/версии/хладагент/Wi-Fi
        elif w.upper() in _KEEP_UPPER:
            out.append(w)                              # акронимы
        elif w.isupper() and len(w) >= 3:
            out.append(w[:1] + w[1:].lower())          # ДЛИННЫЙ КАПС → Капс
        else:
            out.append(w)                              # уже норм
    return " ".join(out)


def _is_inverter(offer: Offer) -> bool:
    nl = f"{offer.model} {offer.series or ''}".lower()
    return "инвертор" in nl or "inverter" in nl


def card_brief(group) -> str:
    """ЧИСТЫЙ короткий текст серии для карточки-картинки (фотоагент рисует его на плашках).
    Заменяет сырой ТТХ-дамп: бренд+серия (в model), тип, размерный ряд, площадь, инвертор.
    Только достоверные структурные данные — без кривых полей БД."""
    rep = group.representative
    type_label = _TYPE_LABEL.get(group.category_id, "Кондиционер")
    sizes = sorted({s for s in (size_from_btu(m.btu_calc, m.category_id) for m in group.members) if s})
    lines = [f"{group.brand} {group.series}".strip(), type_label]
    if len(sizes) > 1:
        lines.append("Типоразмеры: " + " / ".join(str(s) for s in sizes) + " тыс. BTU")
        a0, a1 = _AREA_BY_SIZE.get(sizes[0]), _AREA_BY_SIZE.get(sizes[-1])
        if a0 and a1:
            lines.append(f"Площадь: {a0}–{a1} м²")
    elif sizes:
        a = _AREA_BY_SIZE.get(sizes[0])
        lines.append(f"Мощность: {sizes[0]} тыс. BTU" + (f", до {a} м²" if a else ""))
    lines.append("Инвертор" if _is_inverter(rep) else "Классическая (вкл/выкл)")
    return "\n".join(lines)


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


# Тип для ШАПКИ объявления: без «Настенная», инвертор — впереди (чтобы не обрезалось на «…инвер»).
_TYPE_SHORT = {2: "Сплит-система", 6: "Полупромышленный кондиционер", 7: "Мобильный кондиционер"}


def _header_type(category_id: int | None, inverter: bool) -> str:
    if category_id == 2 and inverter:
        return "Инверторная сплит-система"
    return _TYPE_SHORT.get(category_id, "Кондиционер")


def _area_str(sizes: list[int]) -> str:
    """Площадь для шапки: «до 25 м²» (один размер) или «20–70 м²» (диапазон)."""
    areas = [_AREA_BY_SIZE.get(s) for s in sizes if _AREA_BY_SIZE.get(s)]
    if not areas:
        return ""
    lo, hi = min(areas), max(areas)
    return f"до {hi} м²" if lo == hi else f"{lo}–{hi} м²"


def _fit_title(header: str, name: str, area: str, maxlen: int) -> str:
    """Собрать шапку ≤ maxlen: с площадью если влезает; иначе без площади; иначе подрезать по слову."""
    if area and len(f"{header} {name} ({area})") <= maxlen:
        return f"{header} {name} ({area})"
    base = f"{header} {name}"
    if len(base) <= maxlen:
        return base
    words = base.split()
    while len(words) > 2 and len(" ".join(words)) > maxlen:
        words.pop()
    return " ".join(words)


def _build_title(group, series_disp: str, inv: bool, sizes: list[int], cfg: ContentConfig) -> str:
    header = _header_type(group.category_id, inv)
    tname = series_disp
    if inv:                               # убрать дубль «Inverter/инвертор» из модели — тип уже говорит
        tname = re.sub(r"\b(inverter|инвертор\w*)\b", "", tname, flags=re.I)
        tname = re.sub(r"\s{2,}", " ", tname).strip(" -–")
    name = _smart_title(f"{group.brand} {tname}".strip())
    return _strip_stopwords(_fit_title(header, name, _area_str(sizes), cfg.title_max), cfg.stop_words).strip()


def render_series(group, prices: dict, cfg: ContentConfig) -> Content:
    """Описание ОДНОГО объявления на серию: заголовок + таблица «типоразмер → цена»
    (только в наличии) + продающий текст. `prices` = {supplier_sku члена: цена}.
    `group` — SeriesGroup (duck-typed: brand, series, category_id, members, representative)."""
    rep = group.representative
    seed = _seed(rep)
    type_label = _TYPE_LABEL.get(group.category_id, "Кондиционер")
    inv = _is_inverter(rep)
    series_disp = re.sub(r"\s*\([^)]*\)", "", group.series).strip() or group.series

    # Типоразмер достоверный (btu_calc стандартизован на ingest из мощности кВт) — прямое чтение.
    sz = {m.supplier_sku: size_from_btu(m.btu_calc, m.category_id) for m in group.members}

    by_size: dict[int, int] = {}
    no_size: list[tuple[str, int]] = []
    for m in group.members:
        p = prices.get(m.supplier_sku)
        if not p:
            continue
        size = sz.get(m.supplier_sku)
        if size:
            by_size[size] = min(by_size.get(size, p), p)   # один размер → минимальная цена
        else:
            no_size.append((m.model, p))
    rows = []
    for size in sorted(by_size):
        area = _AREA_BY_SIZE.get(size)
        label = f"{size}000 BTU" + (f" (до {area} м²)" if area else "")
        rows.append(f"• {label} — {_money(by_size[size])}")
    rows += [f"• {model} — {_money(p)}" for model, p in no_size]

    sizes = sorted(by_size)
    title = _build_title(group, series_disp, inv, sizes, cfg)   # шапка: тип+бренд+модель+площадь

    override = (cfg.descriptions or {}).get(getattr(group, "key", None))
    if override:                          # готовый текст (ручной/Codex) + живая таблица цен
        lines = [override.strip()]
        if rows:
            lines += ["", "Цены по типоразмерам (в наличии):"] + rows
    else:                                 # автогенерация описания
        if sizes:
            head = (f"{group.brand} {series_disp}: {type_label.lower()}, "
                    f"типоразмеры {sizes[0]}–{sizes[-1]} тыс. BTU в наличии.")
        else:
            head = f"{group.brand} {series_disp} — {type_label.lower()}."
        lines = [head, "", _benefit(rep, seed)]
        if rows:
            lines += ["", "Цены по типоразмерам (в наличии):"] + rows
        specs = _spec_lines(rep.attrs)
        if specs:
            lines += ["", "Характеристики:"] + specs
        lines += _footer(seed)
    if cfg.website_link and getattr(group, "key", None) in cfg.website_link_keys:
        lines += ["", cfg.website_link]                # ссылка на сайт — только для отмеченных серий
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
