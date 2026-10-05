"""Migrate obsolete XML branches confirmed against Avito's official schemas.

Match the entire old branch: a product name alone must never move an ad.
The same migration runs during new builds and on the preserved shared feed.
"""
from __future__ import annotations


def correct_legacy_taxonomy(tags: dict[str, str]) -> dict[str, str]:
    result = dict(tags)
    if all(tags.get(key) == value for key, value in {
        "Category": "Бытовая электроника",
        "GoodsType": "Аудио и видео",
        "ProductType": "Телевизоры и проекторы",
        "GoodsSubType": "Телевизоры",
    }.items()):
        result.update(Category="Аудио и видео", GoodsType="Телевизоры и проекторы",
                      ProductType="Телевизоры")
        result.pop("GoodsSubType", None)
    # Avito rejected this supplied model with a regional suffix. The official TV
    # schema explicitly permits deriving Model from Title when omitted.
    # Limit this fallback to the exact model rejected in upload 608200915.
    if all(result.get(key) == value for key, value in {
        "Category": "Аудио и видео", "GoodsType": "Телевизоры и проекторы",
        "ProductType": "Телевизоры", "Vendor": "LG", "Model": "32LQ63506LA/ARUG",
    }.items()):
        result.pop("Model", None)
    if all(tags.get(key) == value for key, value in {
        "Category": "Ремонт и строительство",
        "GoodsType": "Инструменты",
        "ToolType": "Силовая, строительная техника и комплектующие",
        "ToolSubType": "Устройства электропитания",
        "DeviceType": "Источники бесперебойного питания",
    }.items()):
        result.update(Category="Оргтехника и расходники", GoodsType="ИБП, сетевые фильтры")
        for key in ("ToolType", "ToolSubType", "DeviceType", "Vendor", "Model"):
            result.pop(key, None)
    if all(tags.get(key) == value for key, value in {
        "Category": "Бытовая техника",
        "GoodsType": "Для кухни",
        "ProductType": "Мелкая кухонная техника",
        "GoodsSubCategory": "Для приготовления пищи",
        "GoodsSubType": "Аэрогрили",
    }.items()):
        result["GoodsSubCategory"] = "Для приготовления блюд"
        result.pop("Model", None)  # This leaf has no Model field.
    return result
