"""Regression checks for the live BTOpT/1C appliance taxonomy."""
from pathlib import Path
from decimal import Decimal

import yaml

from avito_bridge.ingest.price_xls import build_offers
from avito_bridge.config import load_config
from avito_bridge.feed.ad_id import make_ad_id
from avito_bridge.ingest.sources import fetch_profile_offers
from avito_bridge.catalog.series import group_per_item


ROOT = Path(__file__).resolve().parents[1]


def _options() -> dict:
    profile = yaml.safe_load(
        (ROOT / "profiles" / "appliances-btopt.yaml").read_text(encoding="utf-8")
    )
    return profile["profile"]["source_options"]


def _row(article: str, group: str, name: str) -> dict:
    return {
        "article": article,
        "group": group,
        "brand": "",
        "name": name,
        "price": 1000.0,
        "stock": 1,
    }


def _tags(offer) -> dict[str, str]:
    return {
        key.removeprefix("avito_tag:"): value
        for key, value in offer.attrs.items()
        if key.startswith("avito_tag:")
    }


def test_profile_uses_current_avito_taxonomy_for_representative_items():
    offers = build_offers(
        [
            _row(
                "Ту-00000406",
                "Холодильники капельные, Defrost",
                "Холодильник HOMELINE RDF-260DD",
            ),
            _row(
                "Ту-00001952",
                "Блендеры",
                "Блендер Centek CT-1314 1500Вт",
            ),
            _row(
                "Ту-00002196",
                "Кофемашины, кофеварки",
                "Кофеварка Maxwell MW-1657",
            ),
            _row(
                "Ту-00003932",
                "Стабилизаторы напряжения",
                "Стабилизатор Ресанта СПН-13500",
            ),
        ],
        _options(),
    )
    by_article = {
        offer.supplier_sku.removeprefix("pricexls:"): _tags(offer)
        for offer in offers
    }

    assert by_article["Ту-00000406"] == {
        "GoodsType": "Для кухни",
        "ProductType": "Холодильники и морозильные камеры",
        "GoodsSubType": "Холодильники",
    }
    assert {
        "GoodsSubCategory": "Для нарезки и смешивания",
        "GoodsSubType": "Блендеры",
        "BlenderType": "Погружной",
        "Vendor": "Centek",
        "Capacity": "1500",
    }.items() <= by_article["Ту-00001952"].items()
    assert by_article["Ту-00002196"]["GoodsSubType"] == "Кофеварки"
    assert by_article["Ту-00003932"] == {
        "Category": "Ремонт и строительство",
        "GoodsType": "Инструменты",
        "ToolType": "Силовая, строительная техника и комплектующие",
        "ToolSubType": "Устройства электропитания",
        "DeviceType": "Стабилизаторы напряжения",
    }


def test_profile_excludes_known_unready_stock_rows():
    rows = [
        _row(
            "Ту-00002936",
            "Инверторные стиральные машины",
            "Стиральная машина HOMELINE WMCI 8120",
        ),
        _row(
            "Ту-00003551",
            "Микроволновые печи, СВЧ",
            "Микроволновая печь Sakura SA-7055W",
        ),
    ]

    assert build_offers(rows, _options()) == []


def test_profile_explicitly_uses_package_only_without_wallet_fallback():
    profile = yaml.safe_load(
        (ROOT / "profiles" / "appliances-btopt.yaml").read_text(encoding="utf-8")
    )

    assert profile["feed"]["base_tags"]["ListingFee"] == "Package"
    assert "PackageBBL" not in profile["feed"]["base_tags"].values()
    assert "BBL" not in profile["feed"]["base_tags"].values()


def test_profile_restores_confirmed_mbo_sets_with_historical_ids():
    cfg = load_config(ROOT / "profiles" / "appliances-btopt.yaml")
    offers = {
        offer.supplier_sku: offer
        for offer in fetch_profile_offers(cfg)
        if offer.supplier_sku.startswith("manual:mbo-")
    }

    assert set(offers) == {"manual:mbo-07hn1", "manual:mbo-09hn1"}
    assert offers["manual:mbo-07hn1"].stock == 10
    assert offers["manual:mbo-09hn1"].stock == 9
    assert offers["manual:mbo-07hn1"].price_override == Decimal("10590")
    assert offers["manual:mbo-09hn1"].price_override == Decimal("11690")

    anchors = cfg.feed.ad_id_anchor
    keys = {group.representative.supplier_sku: group.key for group in group_per_item(list(offers.values()))}
    assert make_ad_id(anchors[keys["manual:mbo-07hn1"]], "simferopol") == make_ad_id(
        "pricexls:Ту-00003883", "simferopol"
    )
    assert make_ad_id(anchors[keys["manual:mbo-09hn1"]], "simferopol") == make_ad_id(
        "pricexls:Ту-00003885", "simferopol"
    )
