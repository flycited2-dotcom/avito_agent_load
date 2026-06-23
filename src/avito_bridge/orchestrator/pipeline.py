from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from avito_bridge.models import Offer
from avito_bridge.config import AppConfig
from avito_bridge.catalog.catalog import dedup_offers
from avito_bridge.pricing.pricing import compute_price
from avito_bridge.content.render import render_content
from avito_bridge.content.cards import resolve_photos
from avito_bridge.feed.builder import build_ads, build_feed_xml
from avito_bridge.feed.writer import write_atomic


@dataclass
class CycleResult:
    offers_in: int
    ads_built: int
    skipped: int


def run_cycle(offers_provider: Callable[[], list[Offer]], cfg: AppConfig,
              feed_path: Path, state_path: Path) -> CycleResult:
    offers = dedup_offers(offers_provider())
    content: dict[str, tuple[str, str]] = {}
    prices: dict[str, int] = {}
    skipped = 0
    priced_offers: list[Offer] = []
    for o in offers:
        pr = compute_price(o, cfg.pricing)
        if not pr.ok:
            skipped += 1
            continue
        c = render_content(o, cfg.content)
        content[o.supplier_sku] = (c.title, c.description)
        prices[o.supplier_sku] = pr.price
        o.photos = resolve_photos(o, cfg.cards)   # уникальная карточка (если есть) → иначе фото поставщика
        priced_offers.append(o)
    ads = build_ads(priced_offers, cfg.cities, content=content, prices=prices, cfg=cfg.feed)
    write_atomic(build_feed_xml(ads, cfg.feed), feed_path)
    return CycleResult(offers_in=len(offers), ads_built=len(ads), skipped=skipped)
