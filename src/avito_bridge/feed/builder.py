from __future__ import annotations
from dataclasses import dataclass, field
from lxml import etree
from avito_bridge.models import Offer, City, AdRecord
from avito_bridge.feed.ad_id import make_ad_id


@dataclass
class FeedConfig:
    max_active_ads: int = 200
    base_tags: dict = field(default_factory=dict)   # Category/GoodsType/Condition/...
    product_type_map: dict = field(default_factory=dict)  # category_id -> ProductType (Тип климат. оборуд.)
    product_type_default: str = ""
    ac_type_map: dict = field(default_factory=dict)        # category_id -> AirConditionerType (Вид кондиционера)
    ac_subtype_map: dict = field(default_factory=dict)     # category_id -> AirConditionerSubType (Тип кондиционера)
    vendor_map: dict = field(default_factory=dict)         # наш бренд -> имя в справочнике Avito (Производитель)
    vendor_skip: set = field(default_factory=set)          # бренды, которых нет в справочнике Avito → НЕ публиковать


def build_ads(offers: list[Offer], cities: list[City], content: dict[str, tuple[str, str]],
              prices: dict[str, int], cfg: FeedConfig) -> list[AdRecord]:
    ads: list[AdRecord] = []
    for o in offers:
        if o.stock <= 0:
            continue
        if o.supplier_sku not in content or o.supplier_sku not in prices:
            continue
        if o.brand in cfg.vendor_skip:        # бренда нет в справочнике Avito → не публикуем (не ошибка)
            continue
        title, desc = content[o.supplier_sku]
        ptype = cfg.product_type_map.get(o.category_id, cfg.product_type_default)
        ac_t = cfg.ac_type_map.get(o.category_id, "")
        ac_s = cfg.ac_subtype_map.get(o.category_id, "")
        vendor = cfg.vendor_map.get(o.brand, o.brand)
        for city in cities:
            ads.append(AdRecord(
                ad_id=make_ad_id(o.supplier_sku, city.id), supplier_sku=o.supplier_sku,
                city_id=city.id, title=title, description=desc, price=prices[o.supplier_sku],
                address=city.avito_location, product_type=ptype, vendor=vendor,
                ac_type=ac_t, ac_subtype=ac_s, images=list(o.photos), status="pending",
            ))
            if len(ads) >= cfg.max_active_ads:
                return ads
    return ads


def build_feed_xml(ads: list[AdRecord], cfg: FeedConfig) -> str:
    root = etree.Element("Ads", formatVersion="3", target="Avito.ru")
    for a in ads:
        ad = etree.SubElement(root, "Ad")
        etree.SubElement(ad, "Id").text = a.ad_id
        if a.address:
            etree.SubElement(ad, "Address").text = a.address
        for tag, val in cfg.base_tags.items():
            etree.SubElement(ad, tag).text = str(val)
        if a.product_type:
            etree.SubElement(ad, "ProductType").text = a.product_type
        if a.vendor:
            etree.SubElement(ad, "Vendor").text = a.vendor
        if a.ac_type:
            etree.SubElement(ad, "AirConditionerType").text = a.ac_type
        if a.ac_subtype:
            etree.SubElement(ad, "AirConditionerSubType").text = a.ac_subtype
        etree.SubElement(ad, "Title").text = a.title
        etree.SubElement(ad, "Description").text = a.description
        etree.SubElement(ad, "Price").text = str(a.price)
        if a.images:
            imgs = etree.SubElement(ad, "Images")
            for url in a.images:
                etree.SubElement(imgs, "Image", url=url)
    body = etree.tostring(root, encoding="unicode", pretty_print=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body
