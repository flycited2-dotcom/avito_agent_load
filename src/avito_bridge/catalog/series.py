"""Группировка моделей в СЕРИИ: одно объявление на серию (а не на каждый типоразмер).
Бизнес-смысл: клиенты всё равно спрашивают другую мощность; одна карточка на серию
снимает блок Avito «повторное размещение» по фото. См. §P2.

Серия = (source, brand, series). Модели без серии — каждая отдельной «серией» (по модели).
Внутри серии члены сортируются по типоразмеру (BTU)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from avito_bridge.models import Offer
from avito_bridge.content.sizing import size_from_btu

# Хладагент-маркеры (R32/R410A/R290/R22/R134a) — варианты-близнецы серии (напр. Paramount и
# Paramount R32 — те же кондиционеры, та же цена). Убираем их и скобки/билингву при группировке,
# чтобы такие серии схлопывались в ОДНУ. Инвертор/он-офф НЕ трогаем — это разные продукты.
_REFRIG = re.compile(r'(?<![A-Za-zА-Яа-я0-9])R[\s\-]?(?:32|410A?|290|22|134A)(?![A-Za-z0-9])', re.I)


def clean_series(s: str) -> str:
    """Отображаемое имя серии без хладагента и скобок (Paramount R32 → Paramount)."""
    s = re.sub(r'\s*\([^)]*\)', '', s or '')      # скобки (билингва/латиница)
    s = _REFRIG.sub('', s)                         # хладагент R32/R410A/…
    s = re.sub(r'[\s_\-]+', ' ', s).strip(' -–·')
    return s


@dataclass
class SeriesGroup:
    key: str                 # стабильный ключ серии
    source: str
    brand: str
    series: str
    category_id: int | None
    members: list[Offer] = field(default_factory=list)   # модели серии (разные размеры), в наличии

    @property
    def representative(self) -> Offer:
        """Репрезентативная модель серии (младший размер) — для фото/ТТХ/категорийных полей."""
        return self.members[0]

    @property
    def supplier_sku(self) -> str:
        """SKU объявления-серии = SKU репрезентативной модели (стабилен → ad_id и имя карточки)."""
        return self.representative.supplier_sku


def series_key(o: Offer) -> str:
    s = clean_series(o.series or "")
    if s:
        return f"{o.source}|{(o.brand or '').strip().lower()}|{s.lower()}"
    return f"{o.source}|model|{o.supplier_sku}"   # нет серии → отдельная «серия» по модели


def _msize(o: Offer) -> int:
    return size_from_btu(o.btu_calc, o.category_id) or 0


def group_by_series(offers: list[Offer]) -> list[SeriesGroup]:
    """Список SeriesGroup. Внутри — модели по возрастанию типоразмера; группы — в порядке
    первого появления (детерминированно)."""
    groups: dict[str, SeriesGroup] = {}
    for o in offers:
        k = series_key(o)
        g = groups.get(k)
        if g is None:
            g = SeriesGroup(key=k, source=o.source, brand=(o.brand or "").strip(),
                            series=clean_series(o.series or "") or (o.model or "").strip(),
                            category_id=o.category_id)
            groups[k] = g
        g.members.append(o)
    for g in groups.values():
        g.members.sort(key=lambda m: (_msize(m), m.model or ""))
    return list(groups.values())
