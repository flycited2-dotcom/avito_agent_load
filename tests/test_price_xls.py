"""Адаптер price_xls на полностью синтетических строках поставщика."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

import avito_bridge.ingest.price_xls as price_xls
from avito_bridge.feed.builder import FeedConfig, build_ads, build_feed_xml
from avito_bridge.ingest.price_xls import _clean_model, build_offers, parse_price_xls
from avito_bridge.models import City

SAMPLE_ROWS = [
    {
        "article": "K-001",
        "group": "Электрочайники",
        "brand": "TEST",
        "name": "100001 Чайник TEST K1",
        "price": 1000.0,
        "stock_label": "Под заказ",
    },
    {
        "article": "K-002",
        "group": "Электрочайники",
        "brand": "TEST",
        "name": "100002 Чайник TEST K2",
        "price": 1200.0,
        "stock_label": "Под заказ",
    },
    {
        "article": "F-001",
        "group": "Холодильники с нижней морозильной камерой",
        "brand": "TEST",
        "name": "200001 Холодильник TEST F1",
        "price": 20000.0,
        "stock_label": "Под заказ",
    },
    {
        "article": "X-001",
        "group": "Непубликуемая тестовая группа",
        "brand": "TEST",
        "name": "300001 Тестовый товар",
        "price": 500.0,
        "stock_label": "",
    },
]

OPTS = {
    "selected_groups": ["Электрочайники", "Холодильники с нижней морозильной камерой"],
    "group_tags": {
        "Электрочайники": {"GoodsType": "Для кухни", "GoodsSubType": "Мелкая кухонная техника"},
        "Холодильники с нижней морозильной камерой":
            {"GoodsType": "Для кухни", "GoodsSubType": "Холодильники и морозильные камеры"},
    },
    "description_template": "{model}\n\nНовый, гарантия. Группа: {group}.",
}


def test_parse_synthetic_price_xls(monkeypatch, tmp_path):
    source = tmp_path / "synthetic.xls"
    source.write_bytes(b"synthetic workbook placeholder")
    values = [
        ["Код", "Группа", "Производитель", "Номенклатура", "Цена", "Наличие"],
        ["служебная", "", "", "", "", ""],
        ["K-001", "Электрочайники", "TEST", "100001 Чайник TEST K1", 1000, "Под заказ"],
        ["F-001", "Холодильники", "TEST", "200001 Холодильник TEST F1", 20000, ""],
        ["BAD", "Группа", "TEST", "Товар без цены", "не число", ""],
    ]

    class FakeSheet:
        nrows = len(values)

        @staticmethod
        def row_values(index):
            return values[index]

    class FakeBook:
        released = False

        @staticmethod
        def sheet_by_index(index):
            assert index == 0
            return FakeSheet()

        def release_resources(self):
            self.released = True

    book = FakeBook()
    monkeypatch.setattr(price_xls.xlrd, "open_workbook", lambda _path: book)

    rows = parse_price_xls(source)

    assert rows == [
        {
            "article": "K-001",
            "group": "Электрочайники",
            "brand": "TEST",
            "name": "100001 Чайник TEST K1",
            "price": 1000.0,
            "stock_label": "Под заказ",
        },
        {
            "article": "F-001",
            "group": "Холодильники",
            "brand": "TEST",
            "name": "200001 Холодильник TEST F1",
            "price": 20000.0,
            "stock_label": "",
        },
    ]
    assert book.released is True


def test_parse_rejects_oversized_xls_before_xlrd(monkeypatch, tmp_path):
    source = tmp_path / "supplier.xls"
    source.write_bytes(b"x")
    monkeypatch.setattr(price_xls, "MAX_WORKBOOK_BYTES", 0)
    monkeypatch.setattr(
        price_xls.xlrd,
        "open_workbook",
        lambda *args, **kwargs: pytest.fail("oversized file must not be decoded"),
    )

    with pytest.raises(ValueError, match="50 МБ"):
        parse_price_xls(source)


def test_parse_btopt_stock_xlsx_uses_section_bounds_and_actual_stock(tmp_path):
    from openpyxl import Workbook

    source = tmp_path / "btopt.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Артикул", "", "", "", "Номенклатура", "", "", "", "", "", "", "", "", "Закуп", "от 100 т.руб/мес", "Склад основной ООО", "Склад управленки"])
    sheet.append(["S-0", "", "", "", "0. До выбранного раздела", "", "", "", "", "", "", "", "", "", "", "", ""])
    sheet.append(["S-1", "", "", "", "1. Бытовая техника", "", "", "", "", "", "", "", "", "", "", "", ""])
    sheet.append(["S-2", "", "", "", "1.1 Чайники", "", "", "", "", "", "", "", "", "", "", "", ""])
    sheet.append(["K-1", "", "", "", "Чайник TEST K1", "", "", "", "", "", "", "", "", 1000, 900, 2, 1])
    sheet.append(["S-3", "", "", "", "2.1 Стабилизаторы напряжения", "", "", "", "", "", "", "", "", "", "", "", ""])
    sheet.append(["R-1", "", "", "", "Стабилизатор TEST R1", "", "", "", "", "", "", "", "", 5000, 4500, 0, 4])
    sheet.append(["S-4", "", "", "", "3. Генераторы", "", "", "", "", "", "", "", "", "", "", "", ""])
    sheet.append(["G-1", "", "", "", "Генератор TEST G1", "", "", "", "", "", "", "", "", 9000, 8000, 3, 0])
    workbook.save(source)

    rows = parse_price_xls(source, {
        "layout": "btopt_stock",
        "section_start": "1. Бытовая техника",
        "section_end": "2.1 Стабилизаторы напряжения",
        "price_column": "purchase",
    })

    assert [(row["article"], row["group"], row["price"], row["stock"]) for row in rows] == [
        ("K-1", "Чайники", 1000.0, 3),
        ("R-1", "Стабилизаторы напряжения", 5000.0, 4),
    ]
    assert rows[0]["discount_100k_price"] == 900.0


def test_clean_model_strips_leading_article_and_trailing_junk():
    assert _clean_model("003544 Крышка CAPPELLO стекло/силикон с ручкой, 24см,") == \
        "Крышка CAPPELLO стекло/силикон с ручкой, 24см"
    assert _clean_model("Чайник BRAYER BR1023") == "Чайник BRAYER BR1023"


def test_build_offers_only_selected_groups_with_tags_and_description():
    offers = build_offers(SAMPLE_ROWS, OPTS)
    groups = {o.series for o in offers}
    assert groups == set(OPTS["selected_groups"])       # кондиционеры и посуда не просочились
    o = offers[0]
    assert o.supplier_sku.startswith("pricexls:")
    assert o.source == "price_xls"
    assert o.cost > 0
    assert o.stock == 1                                  # «Под заказ» публикуем осознанно
    assert o.photos == []                                # фото в прайсе нет — блокер боевой публикации
    assert o.attrs["avito_tag:GoodsType"] == "Для кухни"
    assert o.attrs["desc_long"].startswith(o.model)
    assert "Группа: " in o.attrs["desc_long"]


def test_build_offers_applies_article_tags_after_group_tags():
    opts = {
        **OPTS,
        "article_tags": {
            "K-001": {
                "GoodsSubType": "Электрочайники",
                "GoodsSubCategory": "Для приготовления напитков",
            },
        },
        "required_tags_by_group": {
            "Электрочайники": [
                "GoodsType", "GoodsSubCategory", "GoodsSubType",
            ],
        },
    }

    offers = build_offers(SAMPLE_ROWS[:1], opts)
    first = next(o for o in offers if o.supplier_sku == "pricexls:K-001")

    assert first.attrs["avito_tag:GoodsType"] == "Для кухни"
    assert first.attrs["avito_tag:GoodsSubCategory"] == \
        "Для приготовления напитков"
    assert first.attrs["avito_tag:GoodsSubType"] == "Электрочайники"


def test_build_offers_rejects_missing_required_article_tag():
    opts = {
        **OPTS,
        "required_tags_by_group": {
            "Электрочайники": ["ProductType"],
        },
    }

    with pytest.raises(
        ValueError,
        match=r"K-001.*Электрочайники.*ProductType",
    ):
        build_offers(SAMPLE_ROWS, opts)


def test_build_offers_skips_explicitly_excluded_articles_before_tag_checks():
    opts = {
        **OPTS,
        "excluded_articles": ["K-001"],
        "required_tags_by_group": {
            "Электрочайники": ["PerArticleRequired"],
        },
        "article_tags": {
            "K-002": {"PerArticleRequired": "ready"},
        },
    }

    offers = build_offers(SAMPLE_ROWS, opts)

    assert {offer.supplier_sku for offer in offers} == {
        "pricexls:K-002",
        "pricexls:F-001",
    }


def test_build_offers_series_carries_group_for_pricing_rules():
    # наценка по группам идёт через pricing.rules match {series: <группа>} — series обязан быть группой
    offers = build_offers(SAMPLE_ROWS, OPTS)
    kettles = [o for o in offers if o.series == "Электрочайники"]
    fridges = [o for o in offers if o.series == "Холодильники с нижней морозильной камерой"]
    assert len(kettles) == 2 and len(fridges) == 1


def test_build_offers_applies_manual_photo_and_final_price_by_article():
    rows = SAMPLE_ROWS
    article = next(r["article"] for r in rows if r["group"] == "Электрочайники")
    offers = build_offers(
        rows, OPTS,
        manual_photos={article: "https://splithome.ru/static/cf-cards/card.jpg"},
        manual_price_override={article: 4990},
    )
    offer = next(o for o in offers if o.supplier_sku == f"pricexls:{article}")
    assert offer.photos == ["https://splithome.ru/static/cf-cards/card.jpg"]
    assert offer.price_override == Decimal("4990")


def test_build_offers_uses_supplier_photo_only_when_manual_one_is_absent():
    rows = SAMPLE_ROWS[:2]
    offers = build_offers(
        rows, OPTS,
        manual_photos={"K-001": "https://example.test/manual.jpg"},
        supplier_photos={
            "K-001": "https://example.test/supplier-k1.jpg",
            "K-002": "https://example.test/supplier-k2.jpg",
        },
    )
    assert offers[0].photos == ["https://example.test/manual.jpg"]
    assert offers[1].photos == ["https://example.test/supplier-k2.jpg"]


def test_build_offers_keeps_local_card_input_name_out_of_public_photos():
    offer = build_offers(
        SAMPLE_ROWS[:1], OPTS,
        supplier_card_inputs={"K-001": "btopt-00000001.jpg"},
    )[0]
    assert offer.photos == []
    assert offer.attrs["card_input_name"] == "btopt-00000001.jpg"


def test_extra_tags_reach_feed_xml():
    offers = build_offers(SAMPLE_ROWS, OPTS)[:1]
    o = offers[0]
    o.photos = ["https://example.test/appliance.jpg"]
    cities = [City(id="simferopol", name="Симферополь", avito_location="Республика Крым, Симферополь")]
    cfg = FeedConfig(base_tags={"Category": "Бытовая техника"})
    ads = build_ads(offers, cities, content={o.supplier_sku: ("Т", "Д")},
                    prices={o.supplier_sku: 1000}, cfg=cfg)
    xml = build_feed_xml(ads, cfg)
    assert "<GoodsType>Для кухни</GoodsType>" in xml
    assert "<GoodsSubType>Мелкая кухонная техника</GoodsSubType>" in xml
    assert "<Category>Бытовая техника</Category>" in xml


def test_fetch_price_xls_resolves_profile_path_and_applies_manual_values(
    monkeypatch, tmp_path
):
    source = tmp_path / "input" / "supplier.xls"
    source.parent.mkdir()
    source.touch()
    captured = {}
    parsed_rows = [{"article": "A-1"}]

    def fake_parse(path, options):
        captured["parsed_path"] = path
        captured["parse_options"] = options
        return parsed_rows

    monkeypatch.setattr(price_xls, "parse_price_xls", fake_parse)

    def fake_build(rows, options, *, manual_photos, manual_price_override,
                   supplier_photos, supplier_card_inputs):
        captured.update(
            rows=rows,
            options=options,
            manual_photos=manual_photos,
            manual_price_override=manual_price_override,
            supplier_photos=supplier_photos,
            supplier_card_inputs=supplier_card_inputs,
        )
        return ["built-offer"]

    monkeypatch.setattr(price_xls, "build_offers", fake_build)
    options = {"path": "input/supplier.xls", "selected_groups": ["Электрочайники"]}
    cfg = SimpleNamespace(
        source_options=options,
        bridge_root=tmp_path,
        catalog=SimpleNamespace(
            manual_photos={"A-1": "https://example.test/photo.jpg"},
            manual_price_override={"A-1": 4990},
        ),
    )

    assert price_xls.fetch_price_xls(cfg) == ["built-offer"]
    assert captured["parsed_path"] == source
    assert captured["parse_options"] is options
    assert captured["rows"] == parsed_rows
    assert captured["options"] is options
    assert captured["manual_photos"] == {"A-1": "https://example.test/photo.jpg"}
    assert captured["manual_price_override"] == {"A-1": 4990}
    assert captured["supplier_photos"] == {}
    assert captured["supplier_card_inputs"] == {}


@pytest.mark.parametrize("configured_path", ["", "missing.xls"])
def test_fetch_price_xls_reports_missing_source(configured_path, tmp_path):
    cfg = SimpleNamespace(
        source_options={"path": configured_path},
        bridge_root=tmp_path,
        catalog=SimpleNamespace(manual_photos={}, manual_price_override={}),
    )

    with pytest.raises(ValueError, match="файл прайса не найден"):
        price_xls.fetch_price_xls(cfg)
