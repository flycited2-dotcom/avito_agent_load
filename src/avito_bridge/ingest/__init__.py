from __future__ import annotations
from pathlib import Path
from typing import Callable
from decimal import Decimal
from avito_bridge.models import Offer, RawProduct
from avito_bridge.ingest.normalize import to_offer, is_conditioner, CatalogFilter
from avito_bridge.ingest.opt_resolver import resolve_cost
from avito_bridge.ingest.jac_json import load_jac_offers


def collect_offers(raw_db: list[RawProduct], jac_path: Path, flt: CatalogFilter,
                   breez_base_lookup: Callable[[str | None], Decimal | None]) -> list[Offer]:
    offers: list[Offer] = []
    for raw in raw_db:
        if not is_conditioner(raw, flt):
            continue
        breez_base = breez_base_lookup(raw.nc_code) if raw.source == "breeze" else None
        offers.append(to_offer(raw, cost=resolve_cost(raw, breez_base)))
    offers.extend(load_jac_offers(jac_path))
    return offers
