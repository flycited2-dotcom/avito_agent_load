import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from avito_bridge import profile_publish as pp
from avito_bridge.feed.builder import FeedConfig, build_feed_xml
from avito_bridge.feed.taxonomy import correct_legacy_taxonomy
from avito_bridge.models import AdRecord
from avito_bridge.ready_price.taxonomy_repair import repair_legacy_taxonomy


TV = {"Category": "Бытовая электроника", "GoodsType": "Аудио и видео",
      "ProductType": "Телевизоры и проекторы", "GoodsSubType": "Телевизоры"}
UPS = {"Category": "Ремонт и строительство", "GoodsType": "Инструменты",
       "ToolType": "Силовая, строительная техника и комплектующие",
       "ToolSubType": "Устройства электропитания",
       "DeviceType": "Источники бесперебойного питания"}
AERO = {"Category": "Бытовая техника", "GoodsType": "Для кухни",
        "ProductType": "Мелкая кухонная техника",
        "GoodsSubCategory": "Для приготовления пищи", "GoodsSubType": "Аэрогрили"}


def _feed(tmp_path, tags=TV):
    bridge, public = tmp_path / "bridge", tmp_path / "public"
    public.mkdir()
    ad = ET.Element("Ad")
    for tag, value in {"Id": "existing-id", "Title": "Existing product",
                       "Description": "Verified description", "Price": "1234",
                       "Address": "Республика Крым, Симферополь", **tags}.items():
        ET.SubElement(ad, tag).text = value
    ET.SubElement(ET.SubElement(ad, "Images"), "Image", url="https://example.com/card.png")
    root = ET.Element("Ads", formatVersion="3", target="Avito.ru")
    root.append(ad)
    feed = public / "avito-feed.xml"
    feed.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return feed, bridge, public


def test_shared_feed_repair_preserves_content_and_is_idempotent(tmp_path):
    feed, bridge, public = _feed(tmp_path)
    before = ET.parse(feed).getroot().find("Ad")
    result = repair_legacy_taxonomy(feed, bridge, public)
    after = ET.parse(feed).getroot().find("Ad")
    assert after.findtext("Category") == "Аудио и видео"
    assert after.findtext("GoodsType") == "Телевизоры и проекторы"
    assert after.findtext("ProductType") == "Телевизоры"
    assert after.find("GoodsSubType") is None
    for tag in ("Id", "Title", "Description", "Price", "Address", "Images"):
        assert ET.tostring(before.find(tag)) == ET.tostring(after.find(tag))
    assert json.loads((Path(result["backup"]) / "deployment.json").read_text(encoding="utf-8"))["status"] == "committed"
    assert repair_legacy_taxonomy(feed, bridge, public)["status"] == "no_changes"


def test_failed_promotion_restores_original_feed(tmp_path, monkeypatch):
    feed, bridge, public = _feed(tmp_path)
    before = feed.read_bytes()
    apply = pp._apply_operations

    def fail_after_write(operations):
        apply(operations)
        raise OSError("power lost after promotion")

    monkeypatch.setattr(pp, "_apply_operations", fail_after_write)
    with pytest.raises(OSError):
        repair_legacy_taxonomy(feed, bridge, public)
    assert feed.read_bytes() == before


def test_partial_taxonomy_match_does_not_move_another_product():
    tags = {**TV, "GoodsSubType": "Проекторы"}
    assert correct_legacy_taxonomy(tags) == tags
    tags = {**UPS, "DeviceType": "Стабилизаторы напряжения"}
    assert correct_legacy_taxonomy(tags) == tags


def test_ups_does_not_keep_unsupported_tool_and_catalog_fields():
    result = correct_legacy_taxonomy({**UPS, "Vendor": "ExeGate", "Model": "EX295984RUS"})
    assert result == {"Category": "Оргтехника и расходники", "GoodsType": "ИБП, сетевые фильтры"}
    assert correct_legacy_taxonomy(AERO)["GoodsSubCategory"] == "Для приготовления блюд"


def test_new_builds_cannot_reintroduce_old_tv_branch():
    ad = AdRecord(ad_id="existing-id", supplier_sku="supplier:tv", city_id="simferopol",
                  title="Телевизор Asano 28LH5011T", description="New TV", price=1234,
                  images=["https://example.com/card.png"], vendor="Asano",
                  product_type=TV["ProductType"], ac_type="", ac_subtype="",
                  extra_tags={"Model": "28LH5011T"}, address="Республика Крым, Симферополь")
    cfg = FeedConfig(base_tags={key: value for key, value in TV.items() if key != "ProductType"})
    root = ET.fromstring(build_feed_xml([ad], cfg))
    assert root.findtext("Ad/Category") == "Аудио и видео"
    assert root.findtext("Ad/GoodsType") == "Телевизоры и проекторы"
    assert root.findtext("Ad/Model") == "28LH5011T"


def test_rejected_lg_catalog_suffix_uses_documented_title_detection():
    tags = {**TV, "Vendor": "LG", "Model": "32LQ63506LA/ARUG"}
    corrected = correct_legacy_taxonomy(tags)
    assert "Model" not in corrected
    assert corrected["Vendor"] == "LG"
    assert correct_legacy_taxonomy(corrected) == corrected
    other = {**TV, "Vendor": "LG", "Model": "43UT80006LA"}
    assert correct_legacy_taxonomy(other)["Model"] == "43UT80006LA"
