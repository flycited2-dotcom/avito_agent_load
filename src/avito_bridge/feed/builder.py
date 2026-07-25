from __future__ import annotations
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from lxml import etree
from avito_bridge.models import Offer, City, AdRecord
from avito_bridge.feed.ad_id import make_ad_id


@dataclass
class FeedConfig:
    max_active_ads: int = 200
    min_active_ads: int = 0
    max_drop_fraction: float = 1.0
    base_tags: dict = field(default_factory=dict)   # Category/GoodsType/Condition/...
    product_type_map: dict = field(default_factory=dict)  # category_id -> ProductType (Тип климат. оборуд.)
    product_type_default: str = ""
    ac_type_map: dict = field(default_factory=dict)        # category_id -> AirConditionerType (Вид кондиционера)
    ac_subtype_map: dict = field(default_factory=dict)     # category_id -> AirConditionerSubType (Тип кондиционера)
    vendor_map: dict = field(default_factory=dict)         # наш бренд -> имя в справочнике Avito (Производитель)
    vendor_skip: set = field(default_factory=set)          # бренды, которых нет в справочнике Avito → НЕ публиковать
    ad_id_revision: dict = field(default_factory=dict)     # supplier_sku -> N: перевыпуск отклонённого объявления под новым Id
    ad_id_anchor: dict = field(default_factory=dict)       # series_key -> historical supplier_sku


_XML_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_RESERVED_TAGS = frozenset({
    "Ads", "Ad", "Id", "Address", "ProductType", "Vendor",
    "AirConditionerType", "AirConditionerSubType", "Title",
    "Description", "Price", "Images", "Image",
})


def _validate_xml_tag(tag: object, *, origin: str) -> str:
    if not isinstance(tag, str) or not _XML_TAG.fullmatch(tag):
        raise ValueError(f"Недопустимое имя XML-тега {tag!r} ({origin})")
    return tag


def _validate_feed_inputs(ads: list[AdRecord], cfg: FeedConfig) -> None:
    seen_ids: set[str] = set()
    for ad in ads:
        if not ad.ad_id.strip() or not ad.title.strip() or not ad.description.strip():
            raise ValueError(
                f"Объявление {ad.supplier_sku!r} не содержит обязательный текст"
            )
        if ad.price <= 0:
            raise ValueError(
                f"Объявление {ad.ad_id!r} содержит недопустимую цену {ad.price}"
            )
        if not ad.images:
            raise ValueError(f"Объявление {ad.ad_id!r} не содержит изображения")
        for image_url in ad.images:
            if (
                not isinstance(image_url, str)
                or len(image_url) > 4096
                or any(ord(character) < 32 for character in image_url)
            ):
                raise ValueError(
                    f"Объявление {ad.ad_id!r} содержит некорректный URL изображения"
                )
            parsed = urlsplit(image_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError(
                    f"Объявление {ad.ad_id!r} содержит небезопасный URL "
                    f"изображения: {image_url!r}"
                )
        if ad.ad_id in seen_ids:
            raise ValueError(f"Дублирующийся Id объявления: {ad.ad_id!r}")
        seen_ids.add(ad.ad_id)

    base_tags = set()
    for tag in cfg.base_tags:
        valid_tag = _validate_xml_tag(tag, origin="feed.base_tags")
        if valid_tag in _RESERVED_TAGS:
            raise ValueError(
                f"Конфликт XML-тега {valid_tag!r}: feed.base_tags "
                "не может переопределять системное поле"
            )
        base_tags.add(valid_tag)

    for ad in ads:
        for tag in ad.extra_tags:
            valid_tag = _validate_xml_tag(
                tag, origin=f"extra_tags объявления {ad.ad_id!r}"
            )
            if valid_tag in _RESERVED_TAGS or valid_tag in base_tags:
                raise ValueError(
                    f"Конфликт XML-тега {valid_tag!r} в объявлении {ad.ad_id!r}"
                )


def build_ads(offers: list[Offer], cities: list[City], content: dict[str, tuple[str, str]],
              prices: dict[str, int], cfg: FeedConfig) -> list[AdRecord]:
    ads: list[AdRecord] = []
    seen_ids: dict[str, tuple[str, str]] = {}
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
            ad_id = make_ad_id(
                o.supplier_sku, city.id,
                revision=cfg.ad_id_revision.get(o.supplier_sku, 0),
            )
            if ad_id in seen_ids:
                previous_sku, previous_city = seen_ids[ad_id]
                raise ValueError(
                    f"Дублирующийся Id объявления {ad_id!r}: "
                    f"{previous_sku!r}/{previous_city!r} и "
                    f"{o.supplier_sku!r}/{city.id!r}"
                )
            seen_ids[ad_id] = (o.supplier_sku, city.id)
            if len(ads) >= cfg.max_active_ads:
                continue
            ads.append(AdRecord(
                ad_id=ad_id,
                supplier_sku=o.supplier_sku,
                city_id=city.id, title=title, description=desc, price=prices[o.supplier_sku],
                address=city.avito_location, product_type=ptype, vendor=vendor,
                ac_type=ac_t, ac_subtype=ac_s,
                extra_tags={k.split(":", 1)[1]: v for k, v in (o.attrs or {}).items()
                            if k.startswith("avito_tag:")},
                images=list(o.photos), status="pending",
            ))
    return ads


def build_feed_xml(ads: list[AdRecord], cfg: FeedConfig) -> str:
    # Полная проверка выполняется до создания дерева: вызывающий код либо
    # получает готовый XML, либо предсказуемую ValueError и не пишет полфида.
    _validate_feed_inputs(ads, cfg)
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
        for tag in sorted(a.extra_tags):                   # generic-теги профилей (GoodsType/…)
            etree.SubElement(ad, tag).text = a.extra_tags[tag]
        etree.SubElement(ad, "Title").text = a.title
        etree.SubElement(ad, "Description").text = a.description
        etree.SubElement(ad, "Price").text = str(a.price)
        if a.images:
            imgs = etree.SubElement(ad, "Images")
            for url in a.images:
                etree.SubElement(imgs, "Image", url=url)
    body = etree.tostring(root, encoding="unicode", pretty_print=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body
