from decimal import Decimal

import pytest
from PIL import Image

from avito_bridge.models import Offer
from avito_bridge.content.cards import (
    CardConfig,
    card_image_extension,
    card_key,
    existing_card_path,
    has_card,
    legacy_card_key,
    resolve_photos,
)


def _o(sku, photos):
    return Offer(supplier_sku=sku, source="s", brand="B", model="M", category_id=2,
                 btu_calc=7, attrs={}, cost=Decimal("1"), retail_ref=None, stock=1,
                 photos=photos, series=None, content_hash="h")


def _write_image(path, image_format="JPEG", size=(8, 8)):
    Image.new("RGB", size, "white").save(path, format=image_format)


def test_card_key_sanitizes():
    assert card_key("rusklimat:NC-7/9") == "rusklimat__NC-7_9"
    assert card_key("jac:MDV AB 07") == "jac__MDV_AB_07"
    assert card_key("rusklimat:НК-1478151") == "rusklimat__НК-1478151"
    assert card_key("first:SAME") != card_key("second:SAME")
    assert legacy_card_key("rusklimat:НК-1478151") == "НК-1478151"


def test_resolve_url_percent_encodes_cyrillic(tmp_path):
    _write_image(tmp_path / "НК-1478151.jpg")
    cfg = CardConfig(enabled=True, dir=str(tmp_path), base_url="https://x/c", exts=[".jpg"])
    o = _o("rusklimat:НК-1478151", ["https://supplier/p.jpg"])
    assert resolve_photos(o, cfg)[0].startswith("https://x/c/%D0%9D%D0%9A-1478151.jpg?v=")  # +версия mtime


def test_resolve_uses_supplier_photo_when_no_card(tmp_path):
    cfg = CardConfig(enabled=True, dir=str(tmp_path), base_url="https://x/c", exts=[".jpg"])
    o = _o("rusklimat:NC7", ["https://supplier/p.jpg"])
    assert resolve_photos(o, cfg) == ["https://supplier/p.jpg"]


def test_resolve_uses_card_when_present(tmp_path):
    _write_image(tmp_path / "NC7.jpg")
    cfg = CardConfig(enabled=True, dir=str(tmp_path), base_url="https://x/c/", exts=[".jpg"])
    o = _o("rusklimat:NC7", ["https://supplier/p.jpg"])
    assert resolve_photos(o, cfg)[0].startswith("https://x/c/NC7.jpg?v=")   # карточка + версия mtime


def test_resolve_prefers_namespaced_card_but_reads_legacy(tmp_path):
    _write_image(tmp_path / "rusklimat__NC7.jpg")
    _write_image(tmp_path / "NC7.jpg")
    cfg = CardConfig(
        enabled=True, dir=str(tmp_path), base_url="https://x/c", exts=[".jpg"]
    )
    o = _o("rusklimat:NC7", [])
    assert "/rusklimat__NC7.jpg?v=" in resolve_photos(o, cfg)[0]


def test_resolve_disabled_returns_supplier(tmp_path):
    (tmp_path / "NC7.jpg").write_bytes(b"img")
    cfg = CardConfig(enabled=False, dir=str(tmp_path), base_url="https://x/c", exts=[".jpg"])
    o = _o("rusklimat:NC7", ["https://supplier/p.jpg"])
    assert resolve_photos(o, cfg) == ["https://supplier/p.jpg"]


@pytest.mark.parametrize("payload", [b"not-an-image", b"\xff\xd8truncated"])
def test_corrupt_card_is_not_treated_as_existing(tmp_path, payload):
    (tmp_path / "source__NC7.jpg").write_bytes(payload)
    cfg = CardConfig(
        enabled=True, dir=str(tmp_path), base_url="https://x/c", exts=[".jpg"]
    )
    offer = _o("source:NC7", ["https://supplier/fallback.jpg"])

    assert has_card(offer, cfg) is False
    assert resolve_photos(offer, cfg) == ["https://supplier/fallback.jpg"]


def test_card_validator_rejects_non_jpeg_png_and_extension_mismatch(tmp_path):
    gif = tmp_path / "card.jpg"
    _write_image(gif, image_format="GIF")
    with pytest.raises(ValueError, match="JPEG or PNG"):
        card_image_extension(gif)

    disguised_png = tmp_path / "card-too.jpg"
    _write_image(disguised_png, image_format="PNG")
    with pytest.raises(ValueError, match="does not match"):
        card_image_extension(disguised_png)


def test_card_validator_enforces_pixel_limit(tmp_path, monkeypatch):
    card = tmp_path / "card.png"
    _write_image(card, image_format="PNG", size=(3, 2))
    monkeypatch.setattr("avito_bridge.content.cards.MAX_CARD_PIXELS", 5)

    with pytest.raises(ValueError, match="pixel safety limit"):
        card_image_extension(card)


def test_card_validator_turns_pillow_decompression_warning_into_rejection(
    tmp_path, monkeypatch
):
    card = tmp_path / "card.png"
    _write_image(card, image_format="PNG", size=(2, 1))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

    with pytest.raises(ValueError, match="not a valid JPEG/PNG"):
        card_image_extension(card)


def test_existing_card_path_ignores_unsafe_configured_extension(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "source__NC7").mkdir()
    outside = tmp_path / "outside.png"
    _write_image(outside, image_format="PNG")

    assert (
        existing_card_path(
            "source:NC7",
            cards,
            ["/../../outside.png", "..\\..\\outside.png", ".svg", 1],
        )
        is None
    )
