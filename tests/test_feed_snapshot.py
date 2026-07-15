"""Снапшот-тест XML-фида: страховка рефакторинга «универсальных профилей».

Фиксированные Offer'ы + замороженный профиль → run_cycle → XML побайтово равен
golden-файлу. Любое НЕумышленное изменение выхода (порядок тегов, тексты,
цены, фильтры) валит тест ДО того, как уедет в боевой фид.

Осознанное изменение формата: перегенерировать golden командой
    python -m pytest tests/test_feed_snapshot.py --regen-golden
и просмотреть diff golden-файла глазами перед коммитом."""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import pytest
from avito_bridge.config import load_config
from avito_bridge.models import Offer
from avito_bridge.orchestrator.pipeline import run_cycle

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "snapshot_feed_golden.xml"


def _offers() -> list[Offer]:
    """Стабильный набор: серия из 2 размеров, одиночная серия с vendor_map-брендом,
    бренд из vendor_skip (не должен публиковаться), товар без остатка (пропуск)."""
    return [
        Offer(supplier_sku="breeze:НС-0001", source="breeze", brand="FUNAI",
              model="KZDC-SN25", category_id=2, btu_calc=7.0, series="SENSEI 2.0",
              cost=Decimal("25000"), stock=3,
              photos=["https://img.example/funai7.jpg"]),
        Offer(supplier_sku="breeze:НС-0002", source="breeze", brand="FUNAI",
              model="KZDC-SN35", category_id=2, btu_calc=9.0, series="SENSEI 2.0",
              cost=Decimal("28000"), stock=2,
              photos=["https://img.example/funai9.jpg"]),
        Offer(supplier_sku="rusklimat:НС-0003", source="rusklimat",
              brand="EXPERTAIR by ZILON", model="EA-07", category_id=7,
              btu_calc=7.0, series="MOBILE ONE", cost=Decimal("19000"), stock=1,
              photos=["https://img.example/zilon.jpg"]),
        Offer(supplier_sku="breeze:НС-0004", source="breeze", brand="NOAVITO",
              model="X-1", category_id=2, btu_calc=7.0, series="SKIPME",
              cost=Decimal("10000"), stock=5,
              photos=["https://img.example/skip.jpg"]),
        Offer(supplier_sku="breeze:НС-0005", source="breeze", brand="FUNAI",
              model="KZDC-SN50", category_id=2, btu_calc=12.0, series="NOSTOCK",
              cost=Decimal("30000"), stock=0, photos=[]),
    ]


def _build_xml(tmp_path: Path) -> str:
    cfg = load_config(FIXTURES / "snapshot_profile.yaml")
    feed_path = tmp_path / "feed.xml"
    result = run_cycle(_offers, cfg, feed_path=feed_path,
                       state_path=tmp_path / "state.db")
    assert result.ads_built == 2          # SENSEI-серия (схлопнута) + MOBILE ONE
    return feed_path.read_text(encoding="utf-8")


def test_feed_xml_matches_golden(tmp_path, request):
    xml = _build_xml(tmp_path)
    if request.config.getoption("--regen-golden"):
        GOLDEN.write_text(xml, encoding="utf-8")
        pytest.skip("golden перегенерирован — просмотрите diff глазами")
    assert GOLDEN.exists(), (
        "Нет golden-файла. Сгенерируйте: pytest tests/test_feed_snapshot.py --regen-golden")
    assert xml == GOLDEN.read_text(encoding="utf-8"), (
        "XML фида изменился! Если это ОСОЗНАННО — перегенерируйте golden "
        "(--regen-golden) и просмотрите diff. Если нет — рефакторинг сломал фид.")


def test_snapshot_covers_key_invariants(tmp_path):
    """Дублируем критичные инварианты явными проверками — понятная диагностика,
    когда golden-diff большой и нечитабельный."""
    xml = _build_xml(tmp_path)
    assert xml.count("<Ad>") == 2
    assert "NOAVITO" not in xml                      # vendor_skip работает
    assert "NOSTOCK" not in xml                      # без остатка не публикуем
    assert "<Vendor>Zilon</Vendor>" in xml           # vendor_map применён
    assert "<AirConditionerType>Сплит-система</AirConditionerType>" in xml
    assert "<AirConditionerType>Мобильный</AirConditionerType>" in xml
    assert "<Category>Бытовая техника</Category>" in xml
