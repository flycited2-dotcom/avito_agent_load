from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from avito_bridge.models import Offer, PriceResult


def _decimal(raw: Decimal | float | int) -> Decimal:
    """Convert external numeric values without inheriting binary-float noise."""
    return raw if isinstance(raw, Decimal) else Decimal(str(raw))


def round_up_90(raw: Decimal | float | int) -> int:
    """Округление ВВЕРХ до ближайшего числа, оканчивающегося на …90 (порт marked_price)."""
    value = _decimal(raw)
    base = (value / Decimal(100)).to_integral_value(rounding=ROUND_FLOOR) * 100
    candidate = base + 90
    return int(candidate if value <= candidate else candidate + 100)


def round_up(raw: Decimal | float | int, step: int) -> int:
    """Округлить цену вверх до ближайшего положительного шага."""
    if step <= 0:
        raise ValueError("Шаг округления должен быть больше нуля")
    value = _decimal(raw)
    units = (value / Decimal(step)).to_integral_value(rounding=ROUND_CEILING)
    return int(units * step)


def _rounded_price(raw: Decimal | float | int, mode: str) -> int:
    if mode == "none":
        # Avito принимает целые рубли. Даже без «маркетингового» округления
        # округляем рассчитанную цену вверх, иначе int(105.01) незаметно
        # уменьшает заданную наценку.
        return int(_decimal(raw).to_integral_value(rounding=ROUND_CEILING))
    if mode == "up_to_10":
        return round_up(raw, 10)
    if mode == "up_to_90":
        return round_up_90(raw)
    if mode == "up_to_100":
        return round_up(raw, 100)
    raise ValueError(f"Неизвестный режим округления: {mode}")


@dataclass
class PricingConfig:
    default_markup_pct: float = 5
    min_margin_abs: Decimal | int = 0   # 0 = без пола маржи (наценка строго +pct%); см. ТЗ §10
    rounding: str = "up_to_90"
    rules: list[dict] = field(default_factory=list)


def _markup_for(offer: Offer, cfg: PricingConfig) -> float:
    for rule in cfg.rules:
        m = rule.get("match", {})
        if all(getattr(offer, k, None) == v for k, v in m.items()):
            return float(rule["markup_pct"])
    return float(cfg.default_markup_pct)


def compute_price(offer: Offer, cfg: PricingConfig) -> PriceResult:
    if offer.price_override is not None and offer.price_override > 0:   # ручная цена (force_include)
        return PriceResult(ok=True, price=int(offer.price_override), markup_pct=0)
    if offer.cost is None or offer.cost <= 0:
        return PriceResult(ok=False, reason="cost<=0 or missing")
    pct = _markup_for(offer, cfg)
    cost = _decimal(offer.cost)
    raw = cost * (Decimal(1) + _decimal(pct) / Decimal(100))
    min_margin = _decimal(cfg.min_margin_abs)
    min_applied = False
    if raw - cost < min_margin:
        raw = cost + min_margin
        min_applied = True
    price = _rounded_price(raw, cfg.rounding)
    return PriceResult(ok=True, price=price, markup_pct=pct, min_margin_applied=min_applied)
