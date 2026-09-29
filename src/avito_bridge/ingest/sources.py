"""Диспетчер источников товаров: profile.source из конфига → функция-адаптер.

Адаптер: (cfg: AppConfig) -> list[Offer]. Новый бизнес = новый адаптер здесь
+ YAML-профиль; ядро (цены/фид/карточки) не трогается.
См. docs/specs/2026-07-04-universal-business-profiles.md."""
from __future__ import annotations
from pathlib import Path
import re
from typing import Callable
from avito_bridge.config import AppConfig
from avito_bridge.models import Offer


def _offer_is_heat_pump(offer: Offer, cfg: AppConfig) -> bool:
    attrs = offer.attrs or {}
    if attrs.get("meta:is_heat_pump") == "1":
        return True
    try:
        minimum = int(attrs.get("meta:heating_min_temp", ""))
    except (TypeError, ValueError):
        minimum = None
    if minimum is not None and minimum <= cfg.catalog.heat_pump_threshold:
        return True
    text = f"{offer.model} {offer.series or ''}".casefold()
    return "heat pump" in text or "теплов" in text and "насос" in text


def _offer_is_inverter(offer: Offer) -> bool:
    if (offer.attrs or {}).get("meta:is_inverter") == "1":
        return True
    text = f"{offer.model} {offer.series or ''}".casefold()
    return "инвертор" in text or "inverter" in text


def _radiator_tags(offer: Offer) -> dict[str, str] | None:
    """Обязательные поля Avito из структурных ТТХ радиатора SplitHome."""
    attrs = offer.attrs or {}
    radiator_type = str(attrs.get("Тип радиатора", "")).strip()
    radiator_type = {
        "Стальной трубчатый": "Трубчатый",
        "Стальной панельный": "Панельный",
        "Трубчатый": "Трубчатый",
        "Панельный": "Панельный",
        "Секционный": "Секционный",
    }.get(radiator_type, "")
    material = str(attrs.get("Материал", "")).strip()
    if material not in {"Биметалл", "Алюминий", "Сталь", "Чугун"}:
        material = ""
    sections = str(attrs.get("Количество секций", "")).strip()
    if not sections and radiator_type in {"Панельный", "Трубчатый"}:
        sections = "1"
    if not offer.brand or not radiator_type or not material or not sections:
        return None
    return {
        "PriceFor": "Штуку",
        "Brand": offer.brand,
        "RadiatorType": radiator_type,
        "Material": material,
        "SectionQuantity": sections,
    }


def _apply_oasis_taxonomy(offer: Offer, cfg: AppConfig) -> bool:
    """Добавить проверенные теги Avito; False означает неполную классификацию."""
    tags = dict((cfg.catalog.category_tags or {}).get(offer.category_id, {}))
    heat_pump = _offer_is_heat_pump(offer, cfg)
    if offer.category_id not in {2, 6, 7}:
        offer.attrs["meta:skip_vendor"] = "1"
    if offer.category_id in {30, 118}:
        offer.attrs["meta:skip_product_type"] = "1"
    if heat_pump:
        tags.update(cfg.catalog.heat_pump_tags or {})
        offer.attrs["meta:listing_kind"] = "heat_pump"
        offer.attrs["meta:supplier_photos"] = "1"
        offer.attrs["meta:skip_ac_tags"] = "1"
        offer.attrs["meta:skip_vendor"] = "1"
    elif offer.category_id in (cfg.catalog.supplier_photo_category_ids or set()):
        offer.attrs["meta:supplier_photos"] = "1"
    if offer.category_id == 118:
        radiator = _radiator_tags(offer)
        if radiator is None:
            return False
        tags.update(radiator)
    label = (cfg.catalog.category_labels or {}).get(offer.category_id)
    if heat_pump:
        label = "Тепловой насос"
    if label:
        offer.attrs["meta:product_label"] = label
    for tag, value in tags.items():
        offer.attrs[f"avito_tag:{tag}"] = str(value)
    return True


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
    offers = dedup_offers(
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
    filtered: list[Offer] = []
    for offer in offers:
        if offer.category_id in (cfg.catalog.inverter_only_category_ids or set()):
            if not _offer_is_inverter(offer) and not _offer_is_heat_pump(offer, cfg):
                continue
        if _apply_oasis_taxonomy(offer, cfg):
            filtered.append(offer)
    return filtered


def fetch_ritualb2b(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.ritualb2b_site import fetch_ritualb2b as _fetch
    return _fetch(cfg)


def fetch_price_xls(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.price_xls import fetch_price_xls as _fetch
    return _fetch(cfg)


def fetch_carver_xlsx(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.carver_xlsx import fetch_carver_xlsx as _fetch
    return _fetch(cfg)


def fetch_manual_only(cfg: AppConfig) -> list[Offer]:
    """Profiles whose complete inventory lives in catalog.manual_products."""
    return []


def fetch_itp_snapshot(cfg: AppConfig) -> list[Offer]:
    from avito_bridge.ingest.itp_snapshot import fetch_itp_snapshot as _fetch
    return _fetch(cfg)


SOURCES: dict[str, Callable[[AppConfig], list[Offer]]] = {
    "oasis_db": fetch_oasis,
    "ritualb2b_site": fetch_ritualb2b,
    "price_xls": fetch_price_xls,
    "carver_xlsx": fetch_carver_xlsx,
    "manual_only": fetch_manual_only,
    "itp_snapshot": fetch_itp_snapshot,
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
    return [offer for offer in [*supplier, *manual]
            if not _excluded_product(offer, cfg)]


def _excluded_product(offer: Offer, cfg: AppConfig) -> bool:
    """Explicit profile exclusions also cover manual and spreadsheet products."""
    options = getattr(cfg, "source_options", None) or {}
    patterns = options.get("excluded_product_patterns", []) or []
    if any(re.search(pattern, offer.model or "") for pattern in patterns):
        return True
    base_tags = getattr(getattr(cfg, "feed", None), "base_tags", {}) or {}
    device_type = (offer.attrs or {}).get("avito_tag:DeviceType", base_tags.get("DeviceType", ""))
    return device_type in (options.get("excluded_device_types", []) or [])
