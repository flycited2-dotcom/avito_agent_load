from decimal import Decimal

import avito_bridge.ingest.carver_xlsx as carver


ROWS = [
    {"row": 4, "article": "ATS-10000-3PIN", "model": "ATS-10000 3pin",
     "name": "Автомат ввода резерва CARVER ATS-10000 3pin",
     "characteristics": "Мощность: 10 кВт", "price": 11679.0, "kind": "ats"},
    {"row": 7, "article": "PPG-1900IS", "model": "PPG-1900IS",
     "name": "Генератор бензиновый CARVER PPG-1900IS",
     "characteristics": (
         "Номининальная мощность генератора, кВт: 1,8\n"
         "Максимальная мощность генератора, кВт: 2,0\n"
         "Выходное напряжение, В: ~230\n"
         "Рекомендуемое топливо: Бензин АИ92"
     ), "price": 22786.0, "kind": "generator"},
]


def test_sku_for_model_is_stable_and_filename_safe():
    assert carver.sku_for_model("ATS-10000 3pin") == "ATS-10000-3PIN"
    assert carver.sku_for_model("PPG-6500АM") == "PPG-6500AM"


def test_source_path_is_portable_between_checkouts(monkeypatch, tmp_path):
    expected = tmp_path / "data" / "carver" / "arrival.xlsx"
    expected.parent.mkdir(parents=True)
    expected.touch()
    monkeypatch.setattr(carver, "BRIDGE_ROOT", tmp_path)

    assert carver.resolve_source_path("data/carver/arrival.xlsx") == expected.resolve()
    assert carver.resolve_source_path(expected) == expected.resolve()


def test_build_offers_preserves_kind_description_photo_and_override():
    offers = carver.build_offers(
        ROWS, {"description_template": "{name}\n{characteristics}"},
        manual_photos={"PPG-1900IS": "https://example.test/ppg.jpg"},
        manual_price_override={"PPG-1900IS": 29990},
    )
    ats, generator = offers
    assert ats.supplier_sku == "carver:ATS-10000-3PIN"
    assert ats.series == "Автоматика ATS"
    assert generator.source == "carver_xlsx"
    assert generator.brand == "CARVER"
    assert generator.cost == Decimal("22786.0")
    assert generator.photos == ["https://example.test/ppg.jpg"]
    assert generator.price_override == Decimal("29990")
    assert "Максимальная мощность генератора, кВт: 2,0" in generator.attrs["desc_long"]
    assert generator.attrs["avito_tag:Brand"] == "CARVER"
    assert generator.attrs["avito_tag:Model"] == "PPG-1900IS"
    assert generator.attrs["avito_tag:FuelType"] == "Бензин"
    assert generator.attrs["avito_tag:Voltage"] == "220 В"
    assert generator.attrs["avito_tag:RatedPower"] == "1.8"
    assert generator.attrs["avito_tag:MaximumPower"] == "2.0"
    assert "avito_tag:Brand" not in ats.attrs


def test_generator_tags_handle_dual_voltage_and_combined_power_line():
    row = {
        "model": "PPG-TEST",
        "characteristics": (
            "Номин. / макс. мощность альтернатора при 230В, кВт: 8,1 / 9\n"
            "Номин. / макс. мощность альтернатора при 400В, кВт: 9 / 10\n"
            "Выходное напряжение / частота, В/Гц: ~230 / 50, ~400 / 50\n"
            "Рекомендуемое топливо: Бензин АИ92"
        ),
    }
    tags = carver._generator_avito_tags(row)
    assert tags["Voltage"] == "220/380 В"
    assert tags["RatedPower"] == "9"
    assert tags["MaximumPower"] == "10"


def test_generator_tags_use_values_from_avito_catalog_for_new_carver_models():
    expected = {
        "PPG-5100ISE": ("PPG-5100iSE", "220 В", "4.0", "4.5"),
        "PPG-6500AM": ("PPG-6500АM", "220 В", "5.0", "5.5"),
        "PPG-9000R": ("PPG-9000е", "220 В", "7.0", "7.5"),
        "PPG-10000R": ("PPG-10000е", "220 В", "8.0", "8.3"),
        "PPG-13500VR": ("PPG-13500EV", "220 В / 380 В", "8.1", "10.0"),
        "PPG-15000IVR": ("PPG-15000iEV", "220 В / 380 В", "10.0", "11.0"),
    }
    for model, values in expected.items():
        tags = carver._generator_avito_tags({"model": model, "characteristics": ""})
        assert (
            tags["Model"], tags["Voltage"], tags["RatedPower"], tags["MaximumPower"]
        ) == values


class Cell:
    def __init__(self, value=None):
        self.value = value


class Start:
    def __init__(self, row):
        self.row = row


class Anchor:
    def __init__(self, zero_based_row):
        self._from = Start(zero_based_row)


class Picture:
    def __init__(self, zero_based_row, data):
        self.anchor = Anchor(zero_based_row)
        self._bytes = data

    def _data(self):
        return self._bytes


class Sheet:
    max_row = 4
    _images = [Picture(3, b"image-bytes")]

    def cell(self, row, column):
        values = {
            (4, 2): "PPG-1900IS",
            (4, 3): "Генератор бензиновый CARVER PPG-1900IS",
            (4, 5): "Мощность: 2 кВт",
            (4, 6): 22786,
        }
        return Cell(values.get((row, column)))


class Book:
    sheetnames = [carver.SHEET_NAME]
    active = Sheet()

    def __getitem__(self, name):
        assert name == carver.SHEET_NAME
        return self.active


def test_parser_and_embedded_photo_use_same_excel_row(monkeypatch):
    monkeypatch.setattr(carver, "load_workbook", lambda *a, **k: Book())
    parsed = carver.parse_carver_xlsx("ignored.xlsx")
    photos = carver.extract_embedded_photos("ignored.xlsx")
    assert parsed[0]["article"] == "PPG-1900IS"
    assert parsed[0]["row"] == 4
    assert photos == {"PPG-1900IS": b"image-bytes"}
