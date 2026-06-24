from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from avito_bridge.models import Offer
from avito_bridge.config import AppConfig
from avito_bridge.catalog.series import group_by_series
from avito_bridge.pricing.pricing import compute_price
from avito_bridge.content.render import render_series
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
    """ОДНО объявление на СЕРИЮ: модели серии схлопываются в один листинг с таблицей
    «типоразмер → цена» (см. P1). ad_id/карточка — по стабильной репрезентативной модели."""
    groups = group_by_series(offers_provider())
    if cfg.selected_series:                     # курирование: публикуем только отмеченные серии
        groups = [g for g in groups if g.key in cfg.selected_series]
    content: dict[str, tuple[str, str]] = {}
    prices: dict[str, int] = {}
    skipped = 0
    reps: list[Offer] = []
    for g in groups:
        member_prices: dict[str, int] = {}
        for m in g.members:
            pr = compute_price(m, cfg.pricing)
            if pr.ok and m.stock > 0:
                member_prices[m.supplier_sku] = pr.price
        if not member_prices:                 # ни один размер серии не доступен → пропуск
            skipped += 1
            continue
        rep = g.representative                 # стабильная (младший размер) → стабильный ad_id/карточка
        c = render_series(g, member_prices, cfg.content)
        content[rep.supplier_sku] = (c.title, c.description)
        prices[rep.supplier_sku] = min(member_prices.values())   # цена Avito = минимальная
        rep.stock = sum(m.stock for m in g.members)               # серия в наличии (для build_ads)
        rep.photos = resolve_photos(rep, cfg.cards)               # карточка серии / иначе фото поставщика
        reps.append(rep)
    # Серии с УНИКАЛЬНОЙ карточкой — в приоритет: они публикуются без блока «повторное размещение».
    reps.sort(key=lambda r: 0 if any("avito-cards" in p for p in r.photos) else 1)
    ads = build_ads(reps, cfg.cities, content=content, prices=prices, cfg=cfg.feed)
    write_atomic(build_feed_xml(ads, cfg.feed), feed_path)
    return CycleResult(offers_in=len(groups), ads_built=len(ads), skipped=skipped)
