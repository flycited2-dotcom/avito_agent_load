"""Сквозной тест профиля венков: реальная фикстура products.js → run_cycle → XML.
Живой сети нет — офферы строим из фикстуры напрямую (тот же путь, что fetch_ritualb2b,
минус httpx)."""
from pathlib import Path
from avito_bridge.config import load_config
from avito_bridge.ingest.ritualb2b_site import parse_products_js, offers_from_products
from avito_bridge.orchestrator.pipeline import run_cycle

ROOT = Path(__file__).parent.parent
FIXTURE = (Path(__file__).parent / "fixtures" / "ritualb2b_products.js").read_text(encoding="utf-8")


def test_wreaths_profile_builds_feed_from_site_catalog(tmp_path):
    cfg = load_config(ROOT / "profiles" / "wreaths.yaml")
    assert cfg.profile_name == "wreaths"
    assert cfg.source == "ritualb2b_site"
    offers = offers_from_products(parse_products_js(FIXTURE), base_url="https://ritualb2b.ru")
    in_stock = [o for o in offers if o.stock > 0]

    feed_path = tmp_path / "wreaths.xml"
    result = run_cycle(lambda: offers, cfg, feed_path=feed_path,
                       state_path=tmp_path / "state.db")
    xml = feed_path.read_text(encoding="utf-8")

    assert result.ads_built == len(in_stock)          # каждый товар в наличии = объявление
    assert xml.count("<Ad>") == result.ads_built
    assert "Венок «Аврора»" in xml                    # title = model с сайта
    assert "<Price>2300</Price>" in xml               # цена сайта без округления
    assert "api/img.php?f=" in xml                    # фото сайта
    assert "<Category>Для дома и дачи</Category>" in xml
    assert "Сплит-система" not in xml                 # кондиционерная логика не протекла
    assert "AirConditionerType" not in xml
