from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml
from avito_bridge.models import City
from avito_bridge.pricing.pricing import PricingConfig
from avito_bridge.feed.builder import FeedConfig
from avito_bridge.content.render import ContentConfig
from avito_bridge.content.cards import CardConfig
from avito_bridge.content.descriptions import load_descriptions
from avito_bridge.ingest.normalize import CatalogFilter


@dataclass
class AppConfig:
    cities: list[City]
    pricing: PricingConfig
    feed: FeedConfig
    content: ContentConfig
    catalog: CatalogFilter
    cards: CardConfig
    selected_series: frozenset = frozenset()   # whitelist серий (key) для публикации; пусто = все


def load_config(path: Path) -> AppConfig:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cities = [City(**c) for c in d.get("cities", [])]
    p = d.get("pricing", {})
    pricing = PricingConfig(default_markup_pct=p.get("default_markup_pct", 5),
                            min_margin_abs=p.get("min_margin_abs", 0),
                            rounding=p.get("rounding", "up_to_90"), rules=p.get("rules", []))
    f = d.get("feed", {})
    ptmap = {int(k): v for k, v in (f.get("product_type_map", {}) or {}).items()}
    actmap = {int(k): v for k, v in (f.get("ac_type_map", {}) or {}).items()}
    acsmap = {int(k): v for k, v in (f.get("ac_subtype_map", {}) or {}).items()}
    feed = FeedConfig(max_active_ads=f.get("max_active_ads", 200),
                      base_tags=f.get("base_tags", {}),
                      product_type_map=ptmap,
                      product_type_default=f.get("product_type_default", ""),
                      ac_type_map=actmap, ac_subtype_map=acsmap,
                      vendor_map=f.get("vendor_map", {}) or {},
                      vendor_skip=set(f.get("vendor_skip", []) or []))
    cc = d.get("content", {})
    manifest = cc.get("descriptions_manifest", "")
    descriptions = load_descriptions(Path(path).parent.parent / manifest) if manifest else {}
    content = ContentConfig(title_max=cc.get("title_max", 50),
                            description_max=cc.get("description_max", 7000),
                            stop_words=cc.get("stop_words", []),
                            website_link=cc.get("website_link", "") or "",
                            website_link_keys=frozenset(cc.get("website_link_keys", []) or []),
                            descriptions=descriptions)
    cat = d.get("catalog", {})
    catalog = CatalogFilter(report_category_ids=cat.get("report_category_ids", [2, 6, 7]),
                            exclude_title_patterns=cat.get("exclude_title_patterns", []))
    selected_series = frozenset(cat.get("selected_series", []) or [])
    cd = d.get("cards", {})
    cards = CardConfig(enabled=bool(cd.get("enabled", False)), dir=cd.get("dir", ""),
                       base_url=cd.get("base_url", ""),
                       exts=cd.get("exts", [".jpg", ".jpeg", ".png"]),
                       require_for_publish=bool(cd.get("require_for_publish", False)))
    return AppConfig(cities=cities, pricing=pricing, feed=feed, content=content,
                     catalog=catalog, cards=cards, selected_series=selected_series)
