"""Безопасная публикация готового контента прайса небольшими партиями.

Точная модель и факты приходят из content-factory. Перед добавлением объявления
модуль повторно сверяет строку с последним принятым каталогом, полным аккаунтом,
ручными остановками и официальной схемой категории Avito.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
import uuid
import time
import xml.etree.ElementTree as XML

from defusedxml import ElementTree as ET
from PIL import Image, UnidentifiedImageError

from avito_bridge import profile_publish as pp
from .catalog import clean
from .identity import compact, identity_db, proposed_id
from .store import SOURCE


DIRECT_GROUPS = {
    "Сушильные автоматы": "sushilnye_mashiny",
    "Холодильники с нижней морозильной камерой": "xolodilniki",
    "Холодильники с верхней морозильной камерой": "xolodilniki",
    "Холодильники встраиваемые": "xolodilniki",
    "Морозильные лари": "morozilnye_kamery_i_lari",
    "Плиты с газовой духовкой": "plity_2301870",
    "Плиты с электро-духовкой": "plity_2301870",
    "Электрические плиты": "plity_2301870",
    "Комбинированные плиты": "plity_2301870",
    "Духовые шкафы": "dukhovye_shkafy",
    "Поверхности газовые": "varochnye_paneli",
    "Поверхности электрические": "varochnye_paneli",
    "Поверхности домино": "varochnye_paneli",
    "Варочные поверхности": "varochnye_paneli",
    "Вытяжки": "vytyazhki",
    "Вытяжки встраиваемые": "vytyazhki",
    "Вытяжки кухонные": "vytyazhki",
    "Посудомойки узкие (45 см)": "posudomoechnye_mashiny_2304489",
    "Посудомоечные машины встраиваемые": "posudomoechnye_mashiny_2304489",
    "Микроволновые печи": "mikrovolnovye_pechi",
    "Микроволновые печи встраиваемые": "mikrovolnovye_pechi",
    "Духовки настольные": "mini_pechi",
    "Мультиварки": "multivarki",
    "Аэрогрили": "aerogrili",
    "Электрочайники": "elektrochainiki_2303640",
    "Кофемолки": "kofemolki",
    "Пылесосы вертикальные": "vertikalnye",
    "Пылесосы колбовые": "napolnye",
    "Пылесосы роботы": "roboty_pylesosy",
    "Пылесосы с мешком": "napolnye",
    "Фены": "feny_klassicheskie",
    "Машинки для стрижки и триммеры": "mashinki_dlya_strizhki",
    "Приборы для маникюра и педикюра": "drugoe_248",
    "Весы напольные": "drugoe_248",
    "Блендеры": "blendery_2303686",
    "Измельчители": "izmelchiteli_2303684",
    "Мясорубки": "myasorubki_2303688",
    "Соковыжималки": "sokovyzhimalki_2303638",
    "Тостеры": "toster",
    "Блинницы": "blinnicy_melkaya_kuhonnaya_tehnika",
    "Сушилки для овощей и фруктов": "sushilki_dlya_ovoshei_i_fruktov",
    "Весы кухонные": "kukhonnyie_vesy",
    "Плитки настольные": "nastolnye_plity",
    "Хлебопечки": "khlebopechki",
    "Эпиляторы": "epilyatory",
}

WASHING_GROUPS = {
    "Стиральные машины с фронтальной загрузкой",
    "Стиральные машины с сушкой",
    "Стиральные машины полуавтоматические центрифуги",
    "Инверторные стиральные машины",
}
TV_PREFIX = "Телевизоры "


def category_slug(group: str, name: str) -> str | None:
    if group in WASHING_GROUPS:
        return "stiralnye_mashiny"
    if group.startswith(TV_PREFIX):
        return "televizory_1147261"
    if group == "Грили и электрошашлычницы":
        return "elektroshashlychnicy" if "шашлыч" in name.casefold() else "elektrogrili"
    if group == "Кофеварки, кофемашины":
        return "kofemashiny_2303637" if "кофемаш" in name.casefold() else "kofevarki_2303639"
    if group == "Утюги и гладильные системы":
        return "parogeneratory" if any(x in name.casefold() for x in ("пароген", "гладильн")) else "utyugi"
    if group == "Отпариватели и пароочистители":
        return "paroochistiteli" if "пароочист" in name.casefold() else "otparivateli"
    if group == "Приборы для укладки волос":
        value = name.casefold()
        if "фен-щет" in value or "фен-щёт" in value:
            return "fen_shyotki"
        if "мультистайл" in value:
            return "multistailery_2304125"
        if "выпрям" in value:
            return "vypryamiteli"
        if "плой" in value or "гофре" in value:
            return "ploiki_i_gofre_2304130"
        return None
    if group == "Миксеры":
        return "planetarnye_miksery" if "планетар" in name.casefold() else "miksery_2303682"
    if group == "Бутербродницы и вафельницы":
        value = name.casefold()
        if "вафель" in value:
            return "vafelnicy_melkaya_kuhonnaya_tehnika"
        if "мультипек" in value:
            return "multipekari"
        if "бутерброд" in value or "сэндвич" in value:
            return "sendvichnicy"
        return None
    # Эта группа содержит и сами диспоузеры, и кнопки/аксессуары. Для Avito нет
    # отдельного листа; сам прибор допустим в «Другое», аксессуар задерживаем.
    if group == "Измельчители пищевых отходов":
        return None if any(x in name.casefold() for x in ("кнопк", "аксессуар")) else "drugoe_248"
    return DIRECT_GROUPS.get(group)


def _required_xml_fields(entry: dict) -> dict[str, dict]:
    result = {}
    for field in entry["schema"].get("fields", []):
        if "xml" not in field.get("feed_format", []):
            continue
        if any(content.get("required") for content in field.get("content", [])):
            result[field["tag"]] = field
    return result


def _single_value(field: dict) -> str | None:
    values = {
        str(value.get("value"))
        for content in field.get("content", [])
        for value in content.get("values", [])
        if value.get("value") is not None
    }
    return next(iter(values)) if len(values) == 1 else None


def _feature_text(content: dict) -> str:
    return " ".join([content.get("name", ""), *content.get("evidence", {}).get("features", [])])


def _technical_value(tag: str, content: dict) -> str | None:
    text = _feature_text(content).casefold()
    if tag == "HeatType":
        if "газоэлект" in text or "комбинирован" in text:
            return "Газоэлектрический"
        if "электр" in text:
            return "Электрический"
        if "газов" in text:
            return "Газовый"
    if tag == "CookingPanelType":
        if "индукцион" in text:
            return "Индукционная"
        if "комбинирован" in text:
            return "Комбинированная"
        if "газов" in text:
            return "Газовая"
        if "стеклокерами" in text:
            return "Стеклокерамическая"
        if "электр" in text:
            return "Электрическая"
    if tag == "HobType":
        if "комбинирован" in text or "газоэлект" in text:
            return "Комбинированная"
        if "газов" in text:
            return "Газовая"
        if "электр" in text:
            return "Электрическая"
    if tag == "CntConforok":
        match = re.search(r"\b([1-7])\s+(?:газов\w*\s+)?(?:конфор|зон\w*\s+(?:приготов|нагрев))", text)
        return match.group(1) if match else None
    if tag == "BlenderType":
        for word, value in (("погружн", "Погружной"),
                            ("стационарн", "Стационарный"),
                            ("портативн", "Портативный")):
            if word in text:
                return value
    return None


_BASIC = {"Id", "Address", "Title", "Description", "Price", "Images", "Category", "GoodsType", "AdType", "Condition"}


def schema_tags(entry: dict, content: dict) -> tuple[dict[str, str] | None, str | None]:
    """Вернуть только доказуемые обязательные теги либо причину задержки."""
    path = entry["path"]
    tags = {
        "Category": path[1],
        "GoodsType": path[2],
        "AdType": "Товар приобретен на продажу",
        "Condition": "Новое",
        "ListingFee": "Package",
    }
    fields = {field["tag"]: field for field in entry["schema"].get("fields", [])}
    required = _required_xml_fields(entry)
    for tag, field in required.items():
        if tag in _BASIC:
            continue
        value = _single_value(field)
        if value is None and tag == "Vendor":
            value = clean(content.get("brand"))
        if value is None and tag == "Model":
            value = clean(content.get("model"))
        if value is None:
            value = _technical_value(tag, content)
        if not value:
            return None, f"required_avito_field_unverified:{tag}"
        tags[tag] = value
    # Каталожные бренд/модель полезны для точного сопоставления, но добавляются
    # только когда официальная схема этой категории их допускает.
    if "Vendor" in fields and content.get("brand"):
        tags.setdefault("Vendor", clean(content["brand"]))
    if "Model" in fields and content.get("model"):
        tags.setdefault("Model", clean(content["model"]))
    return tags, None


def _valid_content(path: Path, item: dict) -> tuple[dict | None, str | None]:
    try:
        content = json.loads((path / "content.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, "invalid_content_manifest"
    evidence = content.get("evidence") or {}
    if content.get("schema_version") != 1 or content.get("article") != item["article"]:
        return None, "content_identity_mismatch"
    if any(clean(content.get(key)) != clean(item.get(key)) for key in ("brand", "name")):
        return None, "content_identity_mismatch"
    if int(content.get("price") or 0) != int(item["avito_price"]):
        return None, "content_price_is_not_latest"
    required_mode = "ready_tv" if item.get("bucket") == "tv" else "ready_light"
    if content.get("card_mode") != required_mode:
        return None, f"content_style_mismatch:{required_mode}"
    audit = content.get("card_text_audit") or {}
    if audit.get("passed") is not True:
        return None, "verified_card_text_audit_required"
    model = clean(content.get("model"))
    if (evidence.get("exact_model") is not True or not model or
            len(compact(model)) < 4 or not re.search(r"\d", model) or
            not str(evidence.get("source_url", "")).startswith("https://") or
            len(evidence.get("features") or []) < 3):
        return None, "exact_model_evidence_required"
    for filename in (content.get("card"), content.get("original")):
        if not filename or Path(filename).name != filename:
            return None, "unsafe_content_image"
        image = path / filename
        if not image.is_file() or image.stat().st_size < 1024:
            return None, "missing_or_invalid_content_image"
        try:
            with Image.open(image) as opened:
                if min(opened.size) < 400:
                    return None, "insufficient_content_image_resolution"
                if filename == content.get("card") and opened.size != (2048, 1536):
                    return None, "avito_card_must_be_2048x1536"
                opened.verify()
        except (OSError, ValueError, UnidentifiedImageError):
            return None, "missing_or_invalid_content_image"
    return content, None


def _visual_approval_reason(path: Path, content: dict, approvals: dict) -> str | None:
    """Require an independent, image-hash-bound review before publishing."""
    entry = approvals.get(content["article"])
    if not isinstance(entry, dict) or entry.get("passed") is not True:
        return "independent_visual_product_audit_required"
    if (entry.get("brand") != content.get("brand") or
            entry.get("model") != content.get("model")):
        return "visual_product_identity_changed"
    for kind in ("card", "original"):
        expected = entry.get(kind + "_sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            return "independent_visual_product_audit_required"
        actual = hashlib.sha256((path / content[kind]).read_bytes()).hexdigest()
        if actual != expected:
            return "visual_product_image_changed"
    return None


def _title(content: dict) -> str:
    title = clean(content["name"])
    if len(title) <= 100:
        return title
    head = title.split("(", 1)[0].strip()
    if len(head) <= 100:
        return head
    return clean(f"{content['brand']} {content['model']}")[:100]


def _description(content: dict) -> str:
    features = [clean(value).rstrip(".") for value in content["evidence"]["features"] if clean(value)]
    bullets = "\n".join(f"• {value}." for value in features)
    return (f"{_title(content)}\n\nНовый товар. В наличии.\n\n"
            f"Подтверждённые характеристики модели:\n{bullets}\n\n"
            "Цена указана за товар. Самовывоз в Симферополе или доставка по Крыму. "
            "Наличие и комплектацию уточняйте перед заказом.")


def _append_ad(root, content: dict, tags: dict[str, str], image_urls: list[str]) -> str:
    aid = proposed_id(content["article"])
    ad = XML.SubElement(root, "Ad")
    ordered = [
        ("Id", aid), ("Address", "Республика Крым, Симферополь"),
        *tags.items(), ("Title", _title(content)),
        ("Description", _description(content)), ("Price", str(content["price"])),
    ]
    seen = set()
    for tag, value in ordered:
        if tag in seen:
            continue
        seen.add(tag)
        XML.SubElement(ad, tag).text = str(value)
    images = XML.SubElement(ad, "Images")
    for url in image_urls:
        XML.SubElement(images, "Image", {"url": url})
    return aid


def _publication_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS content_publications (
            article TEXT PRIMARY KEY, ad_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, batch_id TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publication_batches (
            batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ad_ids TEXT NOT NULL,
            status TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '{}'
        );
    """)


def _pending_batch(database: Path):
    db = identity_db(database)
    try:
        _publication_tables(db)
        db.commit()
        row = db.execute("SELECT * FROM publication_batches WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _rejected_batches(database: Path) -> dict:
    db = identity_db(database)
    try:
        _publication_tables(db)
        rows = db.execute("SELECT batch_id,ad_ids,receipt FROM publication_batches "
                          "WHERE status='rejected' ORDER BY created_at").fetchall()
    finally:
        db.close()
    codes = set()
    rejected_ads = 0
    active_ads = 0
    for row in rows:
        try:
            receipt = json.loads(row["receipt"])
        except (TypeError, ValueError):
            receipt = []
        by_id = {str(item.get("ad_id")): item for item in receipt
                 if item.get("ad_id")} if isinstance(receipt, list) else {}
        for aid in json.loads(row["ad_ids"]):
            item = by_id.get(aid, {})
            errors = [message for message in item.get("messages") or []
                      if message.get("type") == "error"]
            if item.get("avito_status") == "active" and not errors:
                active_ads += 1
                continue
            rejected_ads += 1
            for message in errors:
                if message.get("code") is not None:
                    codes.add(str(message["code"]))
    return {"count": len(rows), "ads": rejected_ads, "active_ads": active_ads,
            "first_batch_id": rows[0]["batch_id"] if rows else "",
            "error_codes": sorted(codes)}


def _unresolved_batch_ids(database: Path) -> list[str]:
    """Exact feed IDs requiring a fresh per-ad Avito report."""
    db = identity_db(database)
    try:
        _publication_tables(db)
        rows = db.execute("SELECT ad_ids FROM publication_batches "
                          "WHERE status IN ('pending','rejected')").fetchall()
        return sorted({str(aid) for row in rows for aid in json.loads(row["ad_ids"])})
    finally:
        db.close()


def _refresh_rejected_batches(database: Path, report_items: list[dict]) -> list[str]:
    """Release a rejected batch only when every exact ID is active without errors."""
    rows_by_id = {str(row.get("ad_id")): row for row in report_items if row.get("ad_id")}
    if not rows_by_id:
        return []
    db = identity_db(database)
    recovered = []
    try:
        _publication_tables(db)
        batches = db.execute("SELECT batch_id,ad_ids,receipt FROM publication_batches "
                             "WHERE status='rejected' ORDER BY created_at").fetchall()
        db.execute("BEGIN IMMEDIATE")
        for batch in batches:
            ids = json.loads(batch["ad_ids"])
            if not ids:
                continue
            old = json.loads(batch["receipt"])
            known = {str(row.get("ad_id")): row for row in old
                     if row.get("ad_id")} if isinstance(old, list) else {}
            known.update({aid: rows_by_id[aid] for aid in ids if aid in rows_by_id})
            receipt = [known[aid] for aid in ids if aid in known]
            all_active = all(
                aid in rows_by_id
                and rows_by_id[aid].get("avito_status") == "active"
                and not any(message.get("type") == "error"
                            for message in rows_by_id[aid].get("messages") or [])
                for aid in ids
            )
            db.execute("UPDATE publication_batches SET status=?,receipt=? WHERE batch_id=?",
                       ("accepted" if all_active else "rejected",
                        json.dumps(receipt, ensure_ascii=False), batch["batch_id"]))
            if not all_active:
                continue
            db.executemany(
                "UPDATE content_publications SET status='accepted',reason='avito_report_active',updated_at=? "
                "WHERE ad_id=? AND batch_id=?",
                [(datetime.now(timezone.utc).isoformat(), aid, batch["batch_id"]) for aid in ids],
            )
            recovered.append(batch["batch_id"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return recovered


def update_batch_receipt(database: Path, report_items: list[dict]) -> dict:
    """Закрыть предыдущую партию только по постатейному отчёту Avito."""
    batch = _pending_batch(database)
    if not batch:
        return {"status": "none"}
    ids = set(json.loads(batch["ad_ids"]))
    rows = {str(row.get("ad_id")): row for row in report_items if str(row.get("ad_id")) in ids}
    if ids - set(rows):
        return {"status": "pending", "batch_id": batch["batch_id"], "missing": sorted(ids - set(rows))}
    rejected = [aid for aid, row in rows.items()
                if row.get("avito_status") in {"blocked", "rejected"}
                or any(msg.get("type") == "error" for msg in row.get("messages", []))]
    not_active = [aid for aid, row in rows.items()
                  if row.get("avito_status") != "active" and aid not in rejected]
    if not_active:
        return {"status": "pending", "batch_id": batch["batch_id"],
                "not_active": not_active}
    status = "rejected" if rejected else "accepted"
    db = identity_db(database)
    try:
        _publication_tables(db)
        db.execute("UPDATE publication_batches SET status=?,receipt=? WHERE batch_id=?",
                   (status, json.dumps(list(rows.values()), ensure_ascii=False), batch["batch_id"]))
        if rejected:
            db.executemany("UPDATE content_publications SET status='held',reason=? WHERE ad_id=?",
                           [("avito_rejected", aid) for aid in rejected])
        else:
            db.executemany(
                "UPDATE content_publications SET status='accepted',reason='avito_report_active',updated_at=? "
                "WHERE ad_id=? AND batch_id=?",
                [(datetime.now(timezone.utc).isoformat(), aid, batch["batch_id"]) for aid in ids],
            )
        db.commit()
    finally:
        db.close()
    return {"status": status, "batch_id": batch["batch_id"], "rejected": rejected}


def publish_ready_content(database: Path, feed: Path, manual_stops: Path, bridge: Path,
                          public: Path, content_dir: Path, schemas_path: Path,
                          account_items: list[dict], report_items: list[dict],
                          *, latest_upload_status: str = "", max_new: int = 4) -> dict:
    receipt = update_batch_receipt(database, report_items)
    recovered = _refresh_rejected_batches(database, report_items)
    rejected = _rejected_batches(database)
    if rejected["count"]:
        return {"status": "blocked_rejected_batch", "receipt": receipt,
                "rejected": rejected, "recovered_batches": recovered,
                "added": [], "held": {}}
    if receipt["status"] in {"pending", "rejected"}:
        return {"status": "waiting_previous_batch", "receipt": receipt, "added": [], "held": {}}
    if latest_upload_status == "processing":
        return {"status": "waiting_active_upload", "receipt": receipt, "added": [], "held": {}}
    schemas = json.loads(schemas_path.read_text(encoding="utf-8"))["leaves"]
    stops = json.loads(manual_stops.read_text(encoding="utf-8"))["entries"]
    approvals_path = bridge / "state/ready-price/visual-approvals.json"
    approvals = json.loads(approvals_path.read_text(encoding="utf-8")) if approvals_path.is_file() else {}
    if not isinstance(approvals, dict):
        raise ValueError("Invalid visual approval registry")
    root = ET.parse(feed).getroot()
    feed_ids = {ad.findtext("Id") for ad in root.findall("Ad")}
    account_titles = [clean(row.get("title")) for row in account_items]
    stop_titles = [clean(row.get("title")) for row in stops.values()]

    db = identity_db(database)
    try:
        _publication_tables(db)
        catalog_rows = db.execute("SELECT article,data,present FROM catalog WHERE source=?", (SOURCE,)).fetchall()
        items = {row["article"]: {**json.loads(row["data"]), "present": bool(row["present"])} for row in catalog_rows}
        bindings = {row[0] for row in db.execute("SELECT article FROM source_bindings WHERE source=?", (SOURCE,))}
    finally:
        db.close()
    duplicates = Counter((clean(x["brand"]).casefold(), clean(x["name"]).casefold())
                         for x in items.values() if x["present"])

    held: dict[str, str] = {}
    candidates = []
    for directory in sorted(content_dir.iterdir() if content_dir.is_dir() else []):
        if not directory.is_dir() or directory.name not in items:
            continue
        item = items[directory.name]
        article = item["article"]
        aid = proposed_id(article)
        if article in bindings or aid in feed_ids:
            continue
        if not item["present"]:
            held[article] = "absent_from_latest_snapshot"
            continue
        if duplicates[(clean(item["brand"]).casefold(), clean(item["name"]).casefold())] > 1:
            held[article] = "duplicate_source_identity"
            continue
        content, reason = _valid_content(directory, item)
        if reason:
            held[article] = reason
            continue
        reason = _visual_approval_reason(directory, content, approvals)
        if reason:
            held[article] = reason
            continue
        model_key = compact(content["model"])
        if any(model_key in compact(title) for title in account_titles):
            held[article] = "possible_existing_account_ad"
            continue
        if any(model_key in compact(title) for title in stop_titles):
            held[article] = "possible_manually_stopped_ad"
            continue
        slug = category_slug(item["group"], content["name"])
        if not slug or slug not in schemas:
            held[article] = "category_requires_review"
            continue
        tags, reason = schema_tags(schemas[slug], content)
        if reason:
            held[article] = reason
            continue
        candidates.append((directory, item, content, tags))
        if len(candidates) >= max_new:
            break

    if not candidates:
        return {"status": "no_ready_candidates", "receipt": receipt, "added": [], "held": held}

    operations = []
    additions = []
    for directory, item, content, tags in candidates:
        digest = hashlib.sha256((directory / "content.json").read_bytes()).hexdigest()
        prefix = f"{proposed_id(item['article'])}-{digest[:12]}"
        urls = []
        for kind in ("card", "original"):
            source = directory / content[kind]
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg"} else ".png"
            relative = Path("avito-cards") / "ready-price" / f"{prefix}-{kind}{suffix}"
            target = public / relative
            operations.append(pp.LiveOperation(target, source.read_bytes()))
            urls.append("https://splithome.ru/static/" + quote(relative.as_posix(), safe="/-._"))
        aid = _append_ad(root, content, tags, urls)
        additions.append({"article": item["article"], "ad_id": aid, "model": content["model"],
                          "name": item["name"], "brand": item["brand"], "sha256": digest})

    after = XML.tostring(root, encoding="utf-8", xml_declaration=True)
    candidate = bridge / "state/ready-price/content-candidate.xml"
    pp._write_atomic_bytes(candidate, after)
    pp.validate_feed(candidate, len(root.findall("Ad")), max(1, len(root.findall("Ad")) - len(additions)))
    operations.append(pp.LiveOperation(feed, after))
    batch_id = "ready-price-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    backup = bridge / "state/studio-backups" / (batch_id + "-" + str(int(time.time())))
    common = Path(__import__("os").path.commonpath([str(bridge), str(public)]))
    journal = pp._prepare_transaction(operations, backup, common,
                                      {"profile": SOURCE, "batch_id": batch_id,
                                       "articles": [x["article"] for x in additions]})
    try:
        pp._apply_operations(operations)
        pp.validate_feed(feed, len(root.findall("Ad")), max(1, len(root.findall("Ad")) - len(additions)))
    except Exception:
        pp._restore_journal(backup, journal, bridge, public)
        pp._set_journal_status(backup, journal, "rolled_back")
        raise
    pp._set_journal_status(backup, journal, "committed")

    now = datetime.now(timezone.utc).isoformat()
    db = identity_db(database)
    try:
        _publication_tables(db)
        db.execute("BEGIN IMMEDIATE")
        evidence = json.dumps({"kind": "verified_ready_price_content", "batch_id": batch_id}, ensure_ascii=False)
        for value in additions:
            db.execute("INSERT OR IGNORE INTO ad_ownership VALUES (?,?,?,?)",
                       (value["ad_id"], SOURCE, SOURCE, evidence))
            db.execute("INSERT OR IGNORE INTO source_bindings VALUES (?,?,?,?,?,?,?,?)",
                       (SOURCE, value["article"], value["ad_id"], value["name"], value["brand"],
                        value["model"], items[value["article"]]["source_sha256"] if "source_sha256" in items[value["article"]] else
                        db.execute("SELECT sha256 FROM catalog WHERE source=? AND article=?", (SOURCE, value["article"])).fetchone()[0], evidence))
            db.execute("INSERT OR REPLACE INTO content_publications VALUES (?,?,?,?,?,?,?)",
                       (value["article"], value["ad_id"], value["sha256"], "pending", "awaiting_avito_report", batch_id, now))
        db.execute("INSERT INTO publication_batches VALUES (?,?,?,?,?)",
                   (batch_id, now, json.dumps([x["ad_id"] for x in additions]), "pending", "{}"))
        for article, reason in held.items():
            db.execute("INSERT OR REPLACE INTO content_publications VALUES (?,?,?,?,?,?,?)",
                       (article, proposed_id(article), "", "held", reason, None, now))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"status": "committed", "receipt": receipt, "batch_id": batch_id,
            "backup": str(backup), "added": additions, "held": held}
