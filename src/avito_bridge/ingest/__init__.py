from __future__ import annotations
from pathlib import Path
from typing import Callable
from decimal import Decimal
from avito_bridge.models import Offer, RawProduct
from avito_bridge.ingest.normalize import to_offer, is_conditioner, CatalogFilter
from avito_bridge.ingest.opt_resolver import resolve_cost
from avito_bridge.ingest.jac_json import load_jac_offers
from avito_bridge.ingest.jac_json import (
    DEFAULT_FUTURE_SKEW_SECONDS,
    DEFAULT_MAX_AGE_SECONDS,
)


def collect_offers(raw_db: list[RawProduct], jac_path: Path, flt: CatalogFilter,
                   breez_base_lookup: Callable[[str | None], Decimal | None],
                   jac_max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
                   jac_future_skew_seconds: int = DEFAULT_FUTURE_SKEW_SECONDS) -> list[Offer]:
    offers: list[Offer] = []
    for raw in raw_db:
        if not is_conditioner(raw, flt):
            continue
        breez_base = breez_base_lookup(raw.nc_code) if raw.source == "breeze" else None
        offers.append(to_offer(raw, cost=resolve_cost(raw, breez_base)))
    if flt.include_jac_snapshot:
        # Свежий снимок — источник истины для цены/остатка JAC. Карточка сайта
        # при совпадении SKU обогащает его названием, ТТХ и фотографиями.
        site_jac = {o.supplier_sku: o for o in offers if o.source == "jac"}
        snapshot = load_jac_offers(
            jac_path,
            max_age_seconds=jac_max_age_seconds,
            future_skew_seconds=jac_future_skew_seconds,
        )
        snapshot = [
            offer for offer in snapshot
            if offer.category_id in flt.report_category_ids
        ]
        enriched: list[Offer] = []
        for current in snapshot:
            site = site_jac.get(current.supplier_sku)
            if site is not None:
                current.model = site.model or current.model
                current.series = current.series or site.series
                current.btu_calc = site.btu_calc or current.btu_calc
                current.photos = current.photos or list(site.photos)
                current.attrs = {**(site.attrs or {}), **(current.attrs or {})}
            enriched.append(current)
        offers = [o for o in offers if o.source != "jac"] + enriched
    return offers
