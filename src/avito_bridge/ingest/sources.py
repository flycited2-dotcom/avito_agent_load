"""Диспетчер источников товаров: profile.source из конфига → функция-адаптер.

Адаптер: (cfg: AppConfig) -> list[Offer]. Новый бизнес = новый адаптер здесь
+ YAML-профиль; ядро (цены/фид/карточки) не трогается.
См. docs/specs/2026-07-04-universal-business-profiles.md."""
from __future__ import annotations
from pathlib import Path
from typing import Callable
from avito_bridge.config import AppConfig
from avito_bridge.models import Offer


def fetch_oasis(cfg: AppConfig) -> list[Offer]:
    """Кондиционеры: Postgres oasis + JAC-остатки (исторический путь из __main__)."""
    from decouple import config
    from avito_bridge.catalog.catalog import dedup_offers
    from avito_bridge.ingest import collect_offers
    from avito_bridge.ingest.oasis_db import fetch_raw_products
    dsn = {"host": config("DB_HOST", "localhost"), "port": config("DB_PORT", "5432"),
           "dbname": config("DB_NAME"), "user": config("DB_USER"), "password": config("DB_PASSWORD")}
    raw = fetch_raw_products(dsn, crimea=cfg.catalog.crimea_warehouse,
                             cats=cfg.catalog.report_category_ids,
                             deny=cfg.catalog.exclude_title_patterns,
                             force_include=cfg.catalog.force_include,
                             manual_photos=cfg.catalog.manual_photos,
                             manual_price_override=cfg.catalog.manual_price_override,
                             connect_timeout=config("DB_CONNECT_TIMEOUT", default=10, cast=int),
                             statement_timeout_ms=config(
                                 "DB_STATEMENT_TIMEOUT_MS", default=30_000, cast=int))
    jac_path = Path(config("JAC_STOCK_JSON", "/opt/splithub_api_telegram/data/jac_stock_latest.json"))
    return dedup_offers(
        collect_offers(
            raw,
            jac_path,
            cfg.catalog,
            breez_base_lookup=lambda nc: None,
            jac_max_age_seconds=config(
                "JAC_STOCK_MAX_AGE_SECONDS", default=86_400, cast=int
            ),
            jac_future_skew_seconds=config(
                "JAC_STOCK_MAX_FUTURE_SKEW_SECONDS", default=300, cast=int
            ),
        )
    )


def fetch_ritualb2b(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.ritualb2b_site import fetch_ritualb2b as _fetch
    return _fetch(cfg)


def fetch_price_xls(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.price_xls import fetch_price_xls as _fetch
    return _fetch(cfg)


def fetch_carver_xlsx(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.carver_xlsx import fetch_carver_xlsx as _fetch
    return _fetch(cfg)


SOURCES: dict[str, Callable[[AppConfig], list[Offer]]] = {
    "oasis_db": fetch_oasis,
    "ritualb2b_site": fetch_ritualb2b,
    "price_xls": fetch_price_xls,
    "carver_xlsx": fetch_carver_xlsx,
}


def get_source(name: str) -> Callable[[AppConfig], list[Offer]]:
    try:
        return SOURCES[name]
    except KeyError:
        raise ValueError(f"Неизвестный источник товаров: '{name}'. "
                         f"Доступны: {sorted(SOURCES)}") from None


def fetch_profile_offers(cfg: AppConfig) -> list[Offer]:
    """Return supplier and Studio-created products through one shared path."""
    from avito_bridge.ingest.manual_products import build_manual_offers

    supplier = get_source(cfg.source)(cfg)
    manual = build_manual_offers(cfg.catalog.manual_products or {}, cfg)
    return [*supplier, *manual]
