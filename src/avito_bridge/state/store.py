"""SQLite-хранилище состояния объявлений — идемпотентность публикаций.

Помнит хэш контента по ad_id: changed() говорит, менялось ли объявление
с прошлого прогона (новый ad_id тоже считается изменением).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class StateStore:
    """Маппинг ad_id → хэш контента, переживает перезапуски (файл SQLite)."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_state ("
            " ad_id TEXT PRIMARY KEY,"
            " content_hash TEXT NOT NULL)"
        )
        self._conn.commit()

    def changed(self, ad_id: str, content_hash: str) -> bool:
        """True, если объявление новое или его хэш отличается от записанного."""
        row = self._conn.execute(
            "SELECT content_hash FROM ad_state WHERE ad_id = ?", (ad_id,)
        ).fetchone()
        return row is None or row[0] != content_hash

    def record(self, ad_id: str, content_hash: str) -> None:
        """Зафиксировать текущий хэш объявления (upsert)."""
        self._conn.execute(
            "INSERT INTO ad_state (ad_id, content_hash) VALUES (?, ?)"
            " ON CONFLICT(ad_id) DO UPDATE SET content_hash = excluded.content_hash",
            (ad_id, content_hash),
        )
        self._conn.commit()
