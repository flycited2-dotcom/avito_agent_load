from decimal import Decimal
from lxml import etree
import pytest
from avito_bridge.models import Offer, City, AdRecord
from avito_bridge.feed.builder import build_ads, build_feed_xml, FeedConfig

CITIES = [City(id="simferopol", name="Симферополь", avito_location="Республика Крым, Симферополь"),
          City(id="sevastopol", name="Севастополь", avito_location="Севастополь")]
CFG = FeedConfig(max_active_ads=10, base_tags={"Category": "Бытовая техника",
                 "GoodsType": "Климатическое оборудование", "GoodsSubType": "Кондиционеры",
                 "AdType": "Товар приобретен на продажу", "Condition": "Новое"},
                 product_type_map={}, product_type_default="Кондиционеры и запчасти",
                 ac_type_map={2: "Сплит-система", 7: "Мобильный"},
                 ac_subtype_map={2: "Настенный", 7: "Напольный"})


def _o(sku, stock=2):
    return Offer(supplier_sku=sku, source="rusklimat", brand="Ballu", model="X-07",
                 category_id=2, btu_calc=7, attrs={}, cost=Decimal("1"), retail_ref=None,
                 stock=stock, photos=["https://i/1.jpg"], series=None, content_hash="h")


def test_fanout_offers_times_cities():
    ads = build_ads([_o("r:1"), _o("r:2")], CITIES,
                    content={"r:1": ("T1", "D1"), "r:2": ("T2", "D2")},
                    prices={"r:1": 10090, "r:2": 11090}, cfg=CFG)
    assert len(ads) == 4                      # 2 оффера × 2 города
    assert all(isinstance(a, AdRecord) for a in ads)


def test_out_of_stock_excluded():
    ads = build_ads([_o("r:1", stock=0)], CITIES,
                    content={"r:1": ("T", "D")}, prices={"r:1": 10090}, cfg=CFG)
    assert ads == []


def test_max_active_ads_caps():
    offers = [_o(f"r:{i}") for i in range(10)]
    content = {f"r:{i}": ("T", "D") for i in range(10)}
    prices = {f"r:{i}": 10090 for i in range(10)}
    ads = build_ads(offers, CITIES, content=content, prices=prices, cfg=CFG)
    assert len(ads) == 10                      # cap=10, хотя 10×2=20 кандидатов


def test_xml_well_formed_and_has_required_tags():
    ads = build_ads([_o("r:1")], CITIES[:1],
                    content={"r:1": ("Заголовок", "Описание")}, prices={"r:1": 10090}, cfg=CFG)
    xml = build_feed_xml(ads, CFG)
    root = etree.fromstring(xml.encode("utf-8"))
    assert root.tag == "Ads"
    ad = root.find("Ad")
    assert ad.findtext("Id") == ads[0].ad_id
    assert ad.findtext("Title") == "Заголовок"
    assert ad.findtext("Price") == "10090"
    assert ad.findtext("Category") == "Бытовая техника"
    assert ad.findtext("Address") == "Республика Крым, Симферополь"
    assert ad.findtext("AdType") == "Товар приобретен на продажу"
    assert ad.findtext("GoodsSubType") == "Кондиционеры"
    assert ad.findtext("ProductType") == "Кондиционеры и запчасти"
    assert ad.findtext("Vendor") == "Ballu"                     # offer.brand
    assert ad.findtext("AirConditionerType") == "Сплит-система"  # category_id=2
    assert ad.findtext("AirConditionerSubType") == "Настенный"
    assert ad.find("Images/Image").get("url") == "https://i/1.jpg"


def test_default_conditioner_feed_is_byte_for_byte_unchanged():
    ads = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("Заголовок", "Описание")},
        prices={"r:1": 10090},
        cfg=CFG,
    )

    assert build_feed_xml(ads, CFG) == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ads formatVersion="3" target="Avito.ru">\n'
        "  <Ad>\n"
        "    <Id>a198dc419d1cb825e4cf66be</Id>\n"
        "    <Address>Республика Крым, Симферополь</Address>\n"
        "    <Category>Бытовая техника</Category>\n"
        "    <GoodsType>Климатическое оборудование</GoodsType>\n"
        "    <GoodsSubType>Кондиционеры</GoodsSubType>\n"
        "    <AdType>Товар приобретен на продажу</AdType>\n"
        "    <Condition>Новое</Condition>\n"
        "    <ProductType>Кондиционеры и запчасти</ProductType>\n"
        "    <Vendor>Ballu</Vendor>\n"
        "    <AirConditionerType>Сплит-система</AirConditionerType>\n"
        "    <AirConditionerSubType>Настенный</AirConditionerSubType>\n"
        "    <Title>Заголовок</Title>\n"
        "    <Description>Описание</Description>\n"
        "    <Price>10090</Price>\n"
        "    <Images>\n"
        '      <Image url="https://i/1.jpg"/>\n'
        "    </Images>\n"
        "  </Ad>\n"
        "</Ads>\n"
    )


def test_allowlisted_offer_category_overrides_base_tag_in_place_once():
    offer = _o("r:category").model_copy(
        update={"attrs": {"avito_tag:Category": "Электроника"}}
    )
    cfg = FeedConfig(
        base_tags={
            "AdType": "Товар приобретен на продажу",
            "Category": "Бытовая техника",
            "Condition": "Новое",
        },
        overridable_tags={"Category"},
    )
    ads = build_ads(
        [offer],
        CITIES[:1],
        content={offer.supplier_sku: ("T", "D")},
        prices={offer.supplier_sku: 10090},
        cfg=cfg,
    )

    ad = etree.fromstring(build_feed_xml(ads, cfg).encode("utf-8")).find("Ad")
    assert ad is not None
    assert [node.tag for node in ad][2:5] == ["AdType", "Category", "Condition"]
    assert [node.text for node in ad.findall("Category")] == ["Электроника"]


def test_allowlisted_reserved_offer_tags_override_dedicated_fields_once():
    overrides = {
        "ProductType": "Тепловое оборудование",
        "Vendor": "Другой производитель",
        "AirConditionerType": "Моноблок",
        "AirConditionerSubType": "Напольный",
    }
    offer = _o("r:reserved").model_copy(
        update={
            "attrs": {
                f"avito_tag:{tag}": value for tag, value in overrides.items()
            }
        }
    )
    cfg = FeedConfig(
        base_tags={"Category": "Бытовая техника"},
        product_type_default="Кондиционеры и запчасти",
        ac_type_map={2: "Сплит-система"},
        ac_subtype_map={2: "Настенный"},
    )
    ads = build_ads(
        [offer],
        CITIES[:1],
        content={offer.supplier_sku: ("T", "D")},
        prices={offer.supplier_sku: 10090},
        cfg=cfg,
    )

    ad = etree.fromstring(build_feed_xml(ads, cfg).encode("utf-8")).find("Ad")
    assert ad is not None
    for tag, value in overrides.items():
        assert [node.text for node in ad.findall(tag)] == [value]


def test_configured_tag_order_controls_taxonomy_fields():
    offer = _o("r:ordered").model_copy(
        update={
            "attrs": {
                "avito_tag:Category": "Бытовая техника",
                "avito_tag:GoodsType": "Для кухни",
                "avito_tag:GoodsSubType": "Крупная бытовая техника",
                "avito_tag:ProductType": "Холодильники",
            }
        }
    )
    order = [
        "Category",
        "GoodsType",
        "GoodsSubType",
        "ProductType",
        "Vendor",
        "Condition",
    ]
    cfg = FeedConfig(
        base_tags={"Condition": "Новое", "Category": "Старая категория"},
        overridable_tags={"Category", "ProductType"},
        tag_order=order,
        product_type_default="Старый тип",
    )
    ads = build_ads(
        [offer],
        CITIES[:1],
        content={offer.supplier_sku: ("T", "D")},
        prices={offer.supplier_sku: 10090},
        cfg=cfg,
    )

    ad = etree.fromstring(build_feed_xml(ads, cfg).encode("utf-8")).find("Ad")
    assert ad is not None
    assert [node.tag for node in ad][2:8] == order


def test_vendor_map_and_skip():
    cfg = FeedConfig(max_active_ads=10, base_tags={"Category": "Бытовая техника"},
                     vendor_map={"EXPERTAIR by ZILON": "Zilon"}, vendor_skip={"NoName"})

    def mk(sku, brand):
        return Offer(supplier_sku=sku, source="s", brand=brand, model="M", category_id=2,
                     btu_calc=7, attrs={}, cost=Decimal("1"), retail_ref=None, stock=1,
                     photos=["u"], series=None, content_hash="h")

    offers = [mk("a:1", "EXPERTAIR by ZILON"), mk("b:1", "NoName"), mk("c:1", "Ballu")]
    content = {o.supplier_sku: ("T", "D") for o in offers}
    prices = {o.supplier_sku: 1090 for o in offers}
    ads = build_ads(offers, [City(id="s", name="S", avito_location="S")],
                    content=content, prices=prices, cfg=cfg)
    by = {a.supplier_sku: a.vendor for a in ads}
    assert "b:1" not in by              # NoName в vendor_skip → пропущен
    assert by["a:1"] == "Zilon"         # сопоставлен по vendor_map
    assert by["c:1"] == "Ballu"         # как есть


def test_build_ads_rejects_duplicate_generated_ids():
    offer = _o("r:duplicate")
    with pytest.raises(ValueError, match="Дублирующийся Id"):
        build_ads(
            [offer, offer.model_copy(deep=True)],
            CITIES[:1],
            content={offer.supplier_sku: ("T", "D")},
            prices={offer.supplier_sku: 10090},
            cfg=CFG,
        )


def test_build_feed_rejects_duplicate_ids_from_direct_callers():
    ad = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("T", "D")},
        prices={"r:1": 10090},
        cfg=CFG,
    )[0]
    with pytest.raises(ValueError, match="Дублирующийся Id"):
        build_feed_xml([ad, ad.model_copy(deep=True)], CFG)


@pytest.mark.parametrize("tag", ["Bad Tag", "1StartsWithDigit", "tag/slash", ""])
def test_build_feed_rejects_invalid_base_xml_tag(tag):
    cfg = FeedConfig(base_tags={tag: "value"})
    with pytest.raises(ValueError, match="Недопустимое имя XML-тега"):
        build_feed_xml([], cfg)


def test_build_feed_rejects_reserved_and_cross_source_tag_conflicts():
    with pytest.raises(ValueError, match="Конфликт XML-тега 'Title'"):
        build_feed_xml([], FeedConfig(base_tags={"Title": "override"}))

    ad = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("T", "D")},
        prices={"r:1": 10090},
        cfg=CFG,
    )[0]
    ad.extra_tags = {"Category": "Другая категория"}
    with pytest.raises(ValueError, match="Конфликт XML-тега 'Category'"):
        build_feed_xml([ad], CFG)


def test_build_feed_rejects_misspelled_reserved_override():
    ad = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("T", "D")},
        prices={"r:1": 10090},
        cfg=CFG,
    )[0]
    ad.extra_tags = {"vendor": "Подмена"}

    with pytest.raises(ValueError, match="Некорректное системное поле 'vendor'"):
        build_feed_xml([ad], CFG)


def test_feed_config_rejects_invalid_and_duplicate_reserved_configuration():
    with pytest.raises(ValueError, match="недопустимое системное поле 'Title'"):
        FeedConfig(overridable_tags={"Title"})

    with pytest.raises(ValueError, match="повторяющиеся XML-теги"):
        FeedConfig(overridable_tags=["Vendor", "Vendor"])

    with pytest.raises(ValueError, match="повторяющиеся XML-теги"):
        FeedConfig(tag_order=["ProductType", "ProductType"])


@pytest.mark.parametrize(
    "image_url",
    [
        "file:///etc/passwd",
        "data:image/jpeg;base64,AA==",
        "https://user:secret@example.test/image.jpg",
        "relative/image.jpg",
        "https://example.test/image.jpg\nInjected",
    ],
)
def test_build_feed_rejects_unsafe_image_urls(image_url):
    ad = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("T", "D")},
        prices={"r:1": 10090},
        cfg=CFG,
    )[0]
    ad.images = [image_url]

    with pytest.raises(ValueError, match="URL изображения"):
        build_feed_xml([ad], CFG)


def test_build_feed_rejects_missing_image_and_non_positive_price():
    ad = build_ads(
        [_o("r:1")],
        CITIES[:1],
        content={"r:1": ("T", "D")},
        prices={"r:1": 10090},
        cfg=CFG,
    )[0]
    ad.images = []
    with pytest.raises(ValueError, match="не содержит изображения"):
        build_feed_xml([ad], CFG)

    ad.images = ["https://example.test/image.jpg"]
    ad.price = 0
    with pytest.raises(ValueError, match="недопустимую цену"):
        build_feed_xml([ad], CFG)
