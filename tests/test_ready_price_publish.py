import hashlib
import json
from pathlib import Path
from PIL import Image

from avito_bridge.ready_price.identity import identity_db
from avito_bridge.ready_price.publish import (
    _publication_tables, _refresh_rejected_batches, _rejected_batches, _technical_value,
    _unresolved_batch_ids, _valid_content, _visual_approval_reason, category_slug, schema_tags,
    publish_ready_content, update_batch_receipt,
)


SCHEMAS = Path(__file__).resolve().parents[1] / "outputs/avito-schemas-20260921.json"


def _leaf(slug):
    return json.loads(SCHEMAS.read_text(encoding="utf-8"))["leaves"][slug]


def _content(name, brand="Test", model="T-100", features=()):
    return {"name": name, "brand": brand, "model": model,
            "evidence": {"features": list(features)}}


def test_mixed_source_groups_are_classified_by_explicit_product_word():
    assert category_slug("Кофеварки, кофемашины", "Кофемашина BQ C100") == "kofemashiny_2303637"
    assert category_slug("Кофеварки, кофемашины", "Кофеварка BQ C101") == "kofevarki_2303639"
    assert category_slug("Грили и электрошашлычницы", "Электрошашлычница X1") == "elektroshashlychnicy"
    assert category_slug("Бутербродницы и вафельницы", "Вафельница X2") == "vafelnicy_melkaya_kuhonnaya_tehnika"


def test_accessory_to_food_waste_disposer_is_not_published_as_appliance():
    assert category_slug("Измельчители пищевых отходов", "Кнопка для измельчителя NORTEL mbl") is None
    assert category_slug("Измельчители пищевых отходов", "Измельчитель NORTEL X100") == "drugoe_248"


def test_schema_tags_derive_cooktop_type_and_burners_only_from_verified_facts():
    content = _content("Газовая панель il Monte BH-650G-BL-M", "il Monte", "BH-650G-BL-M",
                       ["4 газовые конфорки", "Газ-контроль"])
    tags, reason = schema_tags(_leaf("varochnye_paneli"), content)
    assert reason is None
    assert tags["CookingPanelType"] == "Газовая"
    assert tags["CntConforok"] == "4"
    assert tags["Vendor"] == "il Monte"


def test_schema_tags_use_explicit_heating_zones_and_blender_type():
    hob = _content("Индукционная панель Monsher MHI 6026", "Monsher", "MHI 6026",
                   ["4 зоны нагрева по 180 мм"])
    tags, reason = schema_tags(_leaf("varochnye_paneli"), hob)
    assert reason is None and tags["CntConforok"] == "4"
    blender = _content("Блендер Gorenje HB1000RB", "Gorenje", "HB1000RB",
                       ["Погружной блендер"])
    assert _technical_value("BlenderType", blender) == "Погружной"
    _, reason = schema_tags(_leaf("blendery_2303686"), blender)
    assert reason == "required_avito_field_unverified:Capacity"


def test_schema_holds_washing_machine_when_condition_history_is_unknown():
    tags, reason = schema_tags(
        _leaf("stiralnye_mashiny"),
        _content("Стиральная машина Samsung WW11CGP44CSBLP", "Samsung", "WW11CGP44CSBLP",
                 ["Загрузка 11 кг"]),
    )
    assert tags is None
    assert reason.startswith("required_avito_field_unverified:")


def test_simple_category_uses_all_fixed_official_taxonomy_values():
    tags, reason = schema_tags(
        _leaf("myasorubki_2303688"),
        _content("Мясорубка Oursson MG5530/RD", "Oursson", "MG5530/RD"),
    )
    assert reason is None
    assert tags["Category"] == "Бытовая техника"
    assert tags["GoodsType"] == "Для кухни"
    assert tags["ProductType"] == "Мелкая кухонная техника"
    assert tags["GoodsSubCategory"] == "Для нарезки и смешивания"
    assert tags["GoodsSubType"] == "Мясорубки"
    assert tags["ListingFee"] == "Package"


def test_content_without_passed_text_audit_is_held(tmp_path):
    content = {
        "schema_version": 1, "article": "A-1", "brand": "BQ", "model": "KT100",
        "name": "Чайник BQ KT100", "price": 1050, "card_mode": "ready_light",
        "card": "card.png", "original": "original.png",
        "evidence": {"exact_model": True, "source_url": "https://maker.test/kt100",
                     "features": ["1,7 л", "2200 Вт", "Автоотключение"]},
    }
    Image.effect_noise((2048, 1536), 100).convert("RGB").save(tmp_path / "card.png")
    Image.effect_noise((512, 512), 100).convert("RGB").save(tmp_path / "original.png")
    (tmp_path / "content.json").write_text(json.dumps(content), encoding="utf-8")
    item = {"article": "A-1", "brand": "BQ", "name": "Чайник BQ KT100",
            "avito_price": 1050, "bucket": "small"}
    assert _valid_content(tmp_path, item)[1] == "verified_card_text_audit_required"
    content["card_text_audit"] = {"passed": True, "engine": "tesseract-rus+eng"}
    (tmp_path / "content.json").write_text(json.dumps(content), encoding="utf-8")
    assert _valid_content(tmp_path, item)[1] is None

    Image.new("RGB", (1536, 1536), "white").save(tmp_path / "card.png")
    assert _valid_content(tmp_path, item)[1] == "avito_card_must_be_2048x1536"
    Image.new("RGB", (2048, 1536), "white").save(tmp_path / "card.png")

    Image.effect_noise((64, 64), 100).convert("RGB").save(tmp_path / "original.png")
    assert _valid_content(tmp_path, item)[1] == "insufficient_content_image_resolution"


def test_visual_approval_is_independent_and_bound_to_both_image_bytes(tmp_path):
    card = tmp_path / "card.png"
    original = tmp_path / "original.png"
    card.write_bytes(b"approved-card")
    original.write_bytes(b"approved-original")
    content = {"article": "A-1", "brand": "BQ", "model": "KT100",
               "card": card.name, "original": original.name}
    assert _visual_approval_reason(tmp_path, content, {}) == "independent_visual_product_audit_required"
    approval = {"passed": True, "brand": "BQ", "model": "KT100",
                "card_sha256": hashlib.sha256(card.read_bytes()).hexdigest(),
                "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest()}
    assert _visual_approval_reason(tmp_path, content, {"A-1": approval}) is None
    card.write_bytes(b"wrong-card")
    assert _visual_approval_reason(tmp_path, content, {"A-1": approval}) == "visual_product_image_changed"


def test_next_batch_waits_until_every_previous_ad_is_active(tmp_path):
    database = tmp_path / "catalog.sqlite"
    db = identity_db(database)
    _publication_tables(db)
    db.execute("INSERT INTO publication_batches VALUES (?,?,?,?,?)",
               ("batch-1", "2026-09-22T00:00:00+00:00", '["a","b"]', "pending", "{}"))
    db.commit()
    db.close()

    waiting = update_batch_receipt(database, [
        {"ad_id": "a", "avito_status": "active", "messages": []},
        {"ad_id": "b", "avito_status": "old", "messages": []},
    ])
    assert waiting == {"status": "pending", "batch_id": "batch-1", "not_active": ["b"]}

    accepted = update_batch_receipt(database, [
        {"ad_id": "a", "avito_status": "active", "messages": []},
        {"ad_id": "b", "avito_status": "active", "messages": []},
    ])
    assert accepted["status"] == "accepted"


def test_rejected_batch_blocks_all_later_content_publication(tmp_path):
    database = tmp_path / "catalog.sqlite"
    db = identity_db(database)
    _publication_tables(db)
    receipt = [{"ad_id": "a", "messages": [{"type": "error", "code": 2214}]}]
    db.execute("INSERT INTO publication_batches VALUES (?,?,?,?,?)",
               ("batch-1", "2026-09-22T00:00:00+00:00", '["a"]',
                "rejected", json.dumps(receipt)))
    db.commit()
    db.close()
    result = publish_ready_content(database, tmp_path / "missing.xml",
                                   tmp_path / "stops.json", tmp_path, tmp_path,
                                   tmp_path / "content", tmp_path / "schema.json", [], [])
    assert result["status"] == "blocked_rejected_batch"
    assert result["rejected"]["error_codes"] == ["2214"]
    assert result["rejected"]["ads"] == 1
    assert result["added"] == []


def test_rejected_batch_recovers_only_after_exact_clean_active_report(tmp_path):
    database = tmp_path / "catalog.sqlite"
    db = identity_db(database)
    _publication_tables(db)
    db.execute("INSERT INTO publication_batches VALUES (?,?,?,?,?)",
               ("batch-1", "2026-09-22T00:00:00+00:00", '["a","b"]', "rejected", "[]"))
    db.execute("INSERT INTO publication_batches VALUES (?,?,?,?,?)",
               ("batch-2", "2026-09-22T01:00:00+00:00", '["c"]', "pending", "{}"))
    for aid in ("a", "b"):
        db.execute("INSERT INTO content_publications VALUES (?,?,?,?,?,?,?)",
                   (aid, aid, "hash", "held", "avito_rejected", "batch-1", "2026-09-22"))
    db.commit()
    db.close()
    assert _unresolved_batch_ids(database) == ["a", "b", "c"]
    active_a = {"ad_id": "a", "avito_status": "active", "messages": []}
    active_b = {"ad_id": "b", "avito_status": "active", "messages": []}
    assert _refresh_rejected_batches(database, [active_a]) == []
    assert _refresh_rejected_batches(database, [active_a, {
        **active_b, "messages": [{"type": "error", "code": 2214}],
    }]) == []
    remaining = _rejected_batches(database)
    assert remaining["ads"] == 1
    assert remaining["active_ads"] == 1
    assert remaining["error_codes"] == ["2214"]
    assert _refresh_rejected_batches(database, [active_a, active_b]) == ["batch-1"]
    assert _unresolved_batch_ids(database) == ["c"]
    db = identity_db(database)
    assert db.execute("SELECT status FROM publication_batches WHERE batch_id='batch-1'").fetchone()[0] == "accepted"
    assert {row[0] for row in db.execute(
        "SELECT status FROM content_publications WHERE batch_id='batch-1'")} == {"accepted"}
    db.close()
