from __future__ import annotations
from dataclasses import dataclass
from avito_bridge.models import Offer, Content
from avito_bridge.content.sizing import size_from_btu


@dataclass
class ContentConfig:
    title_max: int = 50
    description_max: int = 7000
    stop_words: list[str] = None


def _strip_stopwords(text: str, stop_words: list[str]) -> str:
    out = text
    for w in (stop_words or []):
        out = out.replace(w, "").replace(w.capitalize(), "")
    return out


def _title(offer: Offer) -> str:
    size = size_from_btu(offer.btu_calc, offer.category_id)
    bits = [b for b in [offer.brand, offer.model] if b]
    base = " ".join(bits)
    if size:
        base = f"{base} {size}000 BTU".strip()
    return base


def render_content(offer: Offer, cfg: ContentConfig) -> Content:
    title = _strip_stopwords(_title(offer), cfg.stop_words)[: cfg.title_max].strip()
    lines = [f"{offer.brand} {offer.model}".strip(), ""]
    if offer.category_id:
        lines.append("Новое, гарантия, доставка по Крыму.")
    for k, v in offer.attrs.items():
        lines.append(f"{k}: {v}")
    desc = _strip_stopwords("\n".join(lines), cfg.stop_words)[: cfg.description_max].strip()
    return Content(title=title, description=desc, from_cache=False)
