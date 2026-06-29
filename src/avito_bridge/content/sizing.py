from __future__ import annotations
import re

# Стандартный ряд типоразмеров (kBTU), как ищет клиент: «семёрка/девятка/двенашка/…».
_STANDARD = [7, 9, 12, 18, 24, 30, 36, 42, 48, 60]

# Порог мощности охлаждения (кВт) → стандартный типоразмер. Границы — в широких «зазорах»
# между классами (7k≈2.05, 9k≈2.6, 12k≈3.5, 18k≈5.0, 24k≈7.0, 30k≈8.8 кВт), поэтому устойчиво.
_KW_THRESHOLDS = [(2.5, 7), (3.05, 9), (4.4, 12), (6.2, 18), (7.95, 24),
                  (9.6, 30), (11.5, 36), (13.5, 42)]


def size_from_kw(kw) -> int | None:
    """Типоразмер (kBTU) из мощности охлаждения в кВт. Это ФИЗИЧЕСКАЯ величина —
    достовернее, чем btu_calc (который у разных поставщиков значит разное)."""
    try:
        v = float(kw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    for thr, size in _KW_THRESHOLDS:
        if v <= thr:
            return size
    return 48


def nearest_standard(n: int) -> int:
    """Ближайший стандартный типоразмер к сырому kBTU (10→9, 13→12, 16→18, 26→24)."""
    return min(_STANDARD, key=lambda s: (abs(s - n), s))


def parse_kw(value: str) -> float | None:
    """Первое число из значения ТТХ: «5.00 (1.90 - 5.20)»→5.0, «3.20 ( - )»→3.2, «2,4»→2.4."""
    if value is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(m.group(0).replace(",", ".")) if m else None


def size_from_btu(btu, category_id: int | None = None, apply_area: bool = False) -> int | None:
    """Типоразмер из уже-стандартизованного btu_calc (на этапе ingest он приведён к стандарту
    через derive_size). Карта площадей по умолчанию ОТКЛЮЧЕНА (была источником ошибок 30→9)."""
    try:
        v = float(btu)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 200:                 # полные BTU → kBTU
        v = v / 1000.0
    n = int(round(v))
    if not 1 <= n <= 200:
        return None
    return n


def derive_size(cool_kw, btu_calc, category_id: int | None = None) -> int | None:
    """Достоверный типоразмер: сначала по мощности охлаждения (кВт), иначе — btu_calc,
    приведённый к ближайшему стандарту. Вызывается на этапе ingest (см. to_offer)."""
    s = size_from_kw(cool_kw)
    if s:
        return s
    raw = size_from_btu(btu_calc, category_id)
    return nearest_standard(raw) if raw else None
