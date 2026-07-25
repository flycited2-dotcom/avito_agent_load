from __future__ import annotations
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from avito_bridge.models import Offer
from avito_bridge.config import AppConfig
from avito_bridge.catalog.series import group_by_series, group_per_item
from avito_bridge.pricing.pricing import compute_price
from avito_bridge.content.render import render_series
from avito_bridge.content.cards import resolve_photos, has_card
from avito_bridge.feed.builder import build_ads, build_feed_xml
from avito_bridge.feed.writer import write_atomic
from avito_bridge.feed.validation import existing_ad_count, validate_count_drop
from avito_bridge.state.store import StateStore


@dataclass
class CycleResult:
    offers_in: int
    ads_built: int
    skipped: int
    changed: int = 0


def run_cycle(offers_provider: Callable[[], list[Offer]], cfg: AppConfig,
              feed_path: Path, state_path: Path) -> CycleResult:
    """ОДНО объявление на СЕРИЮ: модели серии схлопываются в один листинг с таблицей
    «типоразмер → цена». Контент/фото берутся у пригодной модели, но ad_id остаётся
    привязан к историческому anchor SKU серии."""
    offers = offers_provider()
    groups = group_per_item(offers) if cfg.grouping == "per_item" else group_by_series(offers)
    if cfg.selected_series:
        # force_include означает «добавить товар в каталог вне наличия», а не
        # «игнорировать явный whitelist публикации». Иначе снятая в Studio
        # галочка у forced/manual товара не имела никакого эффекта.
        groups = [g for g in groups if g.key in cfg.selected_series]
    content: dict[str, tuple[str, str]] = {}
    prices: dict[str, int] = {}
    skipped = 0
    reps: list[Offer] = []
    for g in groups:
        member_prices: dict[str, int] = {}
        priced_in_stock: list[Offer] = []
        for m in g.members:
            pr = compute_price(m, cfg.pricing)
            if pr.ok and m.stock > 0:
                member_prices[m.supplier_sku] = pr.price
                priced_in_stock.append(m)
        if not member_prices:                 # ни один размер серии не доступен → пропуск
            skipped += 1
            continue

        # Первый размер серии может быть без остатка, цены или фото, поэтому
        # контент и изображения берём у первой пригодной модели.
        source_rep: Offer | None = None
        resolved_rep_photos: list[str] = []
        for candidate in priced_in_stock:
            candidate_is_supplier = (
                g.key in cfg.cards.supplier_photo_series or candidate.source == "manual"
            )
            if (cfg.cards.require_for_publish
                    and not candidate_is_supplier
                    and not has_card(candidate, cfg.cards)):
                continue
            candidate_photos = (
                list(candidate.photos)[: cfg.cards.max_images]
                if candidate_is_supplier
                else resolve_photos(candidate, cfg.cards)
            )
            if not candidate_photos:
                continue
            source_rep = candidate
            resolved_rep_photos = candidate_photos
            break
        if source_rep is None:
            skipped += 1
            continue

        # Pydantic-модель Offer изменяема. Рабочая копия не даёт циклу
        # испортить кэш/результат провайдера полями stock/photos.
        rep = source_rep.model_copy(deep=True)
        rep.stock = sum(m.stock for m in priced_in_stock)
        rep.photos = resolved_rep_photos
        render_group = replace(
            g,
            category_id=rep.category_id,
            members=[rep] + [m for m in g.members if m is not source_rep],
        )
        c = render_series(render_group, member_prices, cfg.content)
        # Baseline Bridge historically used g.representative for make_ad_id.
        # Preserve that identity even when another member supplies the photo.
        identity_sku = cfg.feed.ad_id_anchor.get(
            g.key, g.representative.supplier_sku
        )
        rep.supplier_sku = identity_sku
        content[identity_sku] = (c.title, c.description)
        prices[identity_sku] = min(member_prices.values())   # цена Avito = минимальная
        reps.append(rep)
    # Серии с УНИКАЛЬНОЙ карточкой — в приоритет: они публикуются без блока «повторное размещение».
    reps.sort(key=lambda r: 0 if any("avito-cards" in p for p in r.photos) else 1)
    ads = build_ads(reps, cfg.cities, content=content, prices=prices, cfg=cfg.feed)
    if len(ads) < cfg.feed.min_active_ads:
        raise ValueError(
            "Защитная проверка фида: "
            f"получено {len(ads)} объявлений, минимум профиля — {cfg.feed.min_active_ads}. "
            "Предыдущий фид не заменён."
        )
    baseline_feed = Path(feed_path)
    if cfg.public_feed_path:
        public_feed = Path(cfg.public_feed_path)
        if public_feed.is_file():
            baseline_feed = public_feed
    validate_count_drop(
        len(ads),
        existing_ad_count(baseline_feed),
        cfg.feed.max_drop_fraction,
    )
    write_atomic(build_feed_xml(ads, cfg.feed), feed_path)
    changed = 0
    with StateStore(state_path) as state:
        for ad in ads:
            digest = hashlib.sha256(
                ad.model_dump_json(exclude={"status"}).encode("utf-8")
            ).hexdigest()
            changed += int(state.changed(ad.ad_id, digest))
            state.record(ad.ad_id, digest)
    return CycleResult(
        offers_in=len(groups), ads_built=len(ads), skipped=skipped, changed=changed)
