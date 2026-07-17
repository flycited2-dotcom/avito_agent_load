from __future__ import annotations
import math
from dataclasses import dataclass, field
from decimal import Decimal
from avito_bridge.models import Offer, PriceResult


def round_up_90(raw: float) -> int:
    """Округление ВВЕРХ до ближайшего числа, оканчивающегося на …90 (порт marked_price)."""
    base = (int(raw) // 100) * 100
    return base + 90 if raw <= base + 90 else base + 190


def round_up(raw: float, step: int) -> int:
    """Округлить цену вверх до ближайшего положительного шага."""
    if step <= 0:
        raise ValueError("Шаг округления должен быть больше нуля")
    return int(math.ceil(raw / step) * step)


def _rounded_price(raw: float, mode: str) -> int:
    if mode == "none":
        return int(raw)
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
    cost = float(offer.cost)
    raw = cost * (1 + pct / 100.0)
    min_margin = float(cfg.min_margin_abs)
    min_applied = False
    if raw - cost < min_margin:
        raw = cost + min_margin
        min_applied = True
    price = _rounded_price(raw, cfg.rounding)
    return PriceResult(ok=True, price=price, markup_pct=pct, min_margin_applied=min_applied)
