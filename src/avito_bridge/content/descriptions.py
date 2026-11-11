"""Готовые тексты описаний на серию (ручные/Codex). manifest.json сопоставляет
series_key → имя файла .txt в той же папке. Текст переопределяет автогенерацию
описания в render_series (живая таблица цен дописывается автоматически)."""
from __future__ import annotations
import json
from pathlib import Path


def load_descriptions(manifest_path: str | Path) -> dict[str, str]:
    """{series_key: текст описания} из manifest.json (key→файл) и .txt-файлов рядом.
    Отсутствующий manifest/файл — не ошибка (просто меньше переопределений)."""
    p = Path(manifest_path)
    if not p.exists():
        return {}
    mapping = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("Description manifest must be a JSON object")
    base = p.parent.resolve()
    out: dict[str, str] = {}
    for key, fname in mapping.items():
        if not isinstance(key, str) or not isinstance(fname, str):
            raise ValueError("Description manifest keys and filenames must be strings")
        f = (base / fname).resolve()
        try:
            f.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"Description file escapes the manifest directory: {fname!r}"
            ) from exc
        if f.suffix.lower() != ".txt":
            raise ValueError(f"Description file must use .txt extension: {fname!r}")
        if f.exists():
            if f.stat().st_size > 128 * 1024:
                raise ValueError(f"Description file is too large: {fname!r}")
            text = f.read_text(encoding="utf-8").strip()
            if text:
                out[key] = text
    return out
