from __future__ import annotations
from avito_bridge.models import Offer


def _key(o: Offer) -> tuple[str, str]:
    return ((o.brand or "").strip().lower(), (o.model or "").strip().lower())


def _rank(o: Offer) -> tuple[int, float]:
    in_stock = 1 if o.stock > 0 else 0
    cost = float(o.cost) if o.cost is not None else float("inf")
    return (in_stock, -cost)   # больше = лучше: сначала в наличии, затем дешевле


def dedup_offers(offers: list[Offer]) -> list[Offer]:
    best: dict[tuple[str, str], Offer] = {}
    for o in offers:
        k = _key(o)
        if k not in best or _rank(o) > _rank(best[k]):
            best[k] = o
    return list(best.values())
