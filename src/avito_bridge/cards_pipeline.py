"""Автогенерация карточек: мост к очереди фотоагента (ritualb2b vps_api на 127.0.0.1:8765).

Поток (на том же VPS, что и очередь):
  1) забрать готовые: задачи со status='done' → копируем output/{файл} в avito-cards/{ключ}.jpg;
  2) поставить новые (throttle per_run): для серий без карточки скачиваем фото серии и
     POST /api/submit-job (mode=conditioner, brand, model=серия, specs, chat_id).
Локальный агент (Windows+Chrome) генерит через веб-ChatGPT и кладёт результат в очередь.
Маппинг задача→серия — по input_filename (его возвращает submit-job) в нашей CardJobStore.
"""
from __future__ import annotations
import io
import ipaddress
import os
import sqlite3
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import httpx

from avito_bridge.content.cards import (
    MAX_CARD_BYTES,
    card_image_extension,
    card_input_photo,
    card_key,
    existing_card_path,
    legacy_card_key,
)
from avito_bridge.content.render import card_brief


@dataclass
class FotogenConfig:
    api_url: str
    token: str
    chat_id: int
    queue_db: str
    output_dir: str
    cards_dir: str
    mode: str = "conditioner"  # режим по умолчанию
    modes: dict = None         # {series_key: режим} — переопределяет mode для конкретной серии
    per_run: int = 8           # максимум новых задач за один запуск
    max_pending: int = 15      # потолок «в работе» (чтобы не гнать сотни подряд — риск ToS)
    max_total: int = 100000    # ВСЕГО карточек к генерации (для теста ставим ~20; потом снимем)


# ── очередь-API фотоагента ──────────────────────────────────────────────────
def submit_card_job(cfg: FotogenConfig, photo_bytes: bytes, brand: str, model: str,
                    specs: str, http: httpx.Client | None = None,
                    mode: str | None = None) -> str | None:
    """POST /api/submit-job → имя поставленного входного файла (для маппинга)."""
    if len(photo_bytes) > 15 * 1024 * 1024:
        raise ValueError("Input photo exceeds the 15 MiB safety limit")
    owned_client = http is None
    client = http or httpx.Client(timeout=30)
    try:
        r = client.post(
            f"{cfg.api_url.rstrip('/')}/api/submit-job",
            headers={"x-agent-token": cfg.token},
            data={"mode": mode or cfg.mode, "specs": specs, "brand": brand or "",
                  "model": model or "", "chat_id": str(cfg.chat_id)},
            files={"photo": (f"{(model or 'card')}.jpg".replace(" ", "_"),
                             io.BytesIO(photo_bytes), "image/jpeg")},
        )
        r.raise_for_status()
        return (r.json() or {}).get("queued")
    finally:
        if owned_client:
            client.close()


def _query_jobs(queue_db: str, input_filenames: list[str], status: str) -> dict[str, str]:
    if not input_filenames:
        return {}
    con = sqlite3.connect(f"file:{Path(queue_db).as_posix()}?mode=ro", uri=True)
    try:
        # Run a parameterized lookup per filename.  The queue is deliberately
        # bounded, and avoiding a dynamically sized IN expression keeps the
        # query both portable and immune to accidental SQL construction bugs.
        rows = []
        for input_filename in input_filenames:
            rows.extend(
                con.execute(
                    "SELECT input_filename, output_filename FROM jobs "
                    "WHERE status=? AND input_filename=?",
                    [status, input_filename],
                ).fetchall()
            )
        return {r[0]: r[1] for r in rows}
    finally:
        con.close()


def wake_agent(queue_db: str) -> None:
    """Сигнал WatchDog на локальном ПК: запустить агента (он обработает очередь).
    Тот же механизм, что кнопка «🚀 Запустить агента»: flags.agent_command='start'."""
    con = sqlite3.connect(queue_db, timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO flags (key, value) VALUES ('agent_command', 'start')")
        con.commit()
    finally:
        con.close()


def done_results(queue_db: str, input_filenames: list[str]) -> dict[str, str]:
    return {k: v for k, v in _query_jobs(queue_db, input_filenames, "done").items() if v}


def failed_inputs(queue_db: str, input_filenames: list[str]) -> set[str]:
    return set(_query_jobs(queue_db, input_filenames, "failed").keys())


# ── состояние (маппинг серия→задача) ────────────────────────────────────────
MAX_TRIES = 3   # сколько раз пробуем сгенерировать карточку серии при failed (потом сдаёмся)


class CardJobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.execute("CREATE TABLE IF NOT EXISTS card_jobs "
                      "(key TEXT PRIMARY KEY, input_filename TEXT, status TEXT)")
            try:                       # миграция: счётчик попыток (для авто-ретрая failed)
                c.execute("ALTER TABLE card_jobs ADD COLUMN tries INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass                   # колонка уже есть

    def _c(self):
        return sqlite3.connect(self.path)

    def get(self, key: str):
        """(input_filename, status, tries) или None."""
        with self._c() as c:
            return c.execute("SELECT input_filename, status, COALESCE(tries,0) "
                             "FROM card_jobs WHERE key=?", (key,)).fetchone()

    def pending(self) -> list[tuple[str, str]]:
        with self._c() as c:
            return list(c.execute("SELECT key, input_filename FROM card_jobs WHERE status='pending'"))

    def count(self) -> int:
        with self._c() as c:
            return c.execute("SELECT count(*) FROM card_jobs WHERE status!='failed'").fetchone()[0]

    def record(self, key: str, input_filename: str, status: str, tries: int = 0) -> None:
        with self._c() as c:
            c.execute("INSERT INTO card_jobs(key,input_filename,status,tries) VALUES(?,?,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET input_filename=excluded.input_filename, "
                      "status=excluded.status, tries=excluded.tries",
                      (key, input_filename, status, tries))


_SKIP_SPEC_KEYS = {"Бренд", "Модель", "Серия", "Модель внутреннего блока", "Модель наружного блока"}


def specs_text(attrs: dict, max_lines: int = 8) -> str:
    out = []
    for k, v in attrs.items():
        if k in _SKIP_SPEC_KEYS:
            continue
        v = (v or "").replace("( - )", "").strip()
        if v:
            out.append(f"{k}: {v}")
        if len(out) >= max_lines:
            break
    return "\n".join(out)


def _validate_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Card source must be an HTTP(S) URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local card source URLs are forbidden")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve card source host {hostname!r}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"Non-public card source address is forbidden: {address}")


def _validate_connected_peer(response: httpx.Response) -> None:
    """Reject DNS rebinding by checking the address of the connected socket."""
    network_stream = response.extensions.get("network_stream")
    if network_stream is None or not hasattr(network_stream, "get_extra_info"):
        raise ValueError("Cannot verify the connected card source address")
    peer = network_stream.get_extra_info("server_addr")
    if not peer:
        raise ValueError("Cannot verify the connected card source address")
    address = peer[0] if isinstance(peer, (tuple, list)) else peer
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Invalid connected card source address: {address!r}") from exc
    if not ip.is_global:
        raise ValueError(f"Non-public connected card source address is forbidden: {address}")


def _http_get(url: str) -> bytes:
    _validate_public_http_url(url)
    with httpx.Client(
        headers={"User-Agent": "AvitoBridge/1.0"},
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with client.stream("GET", url) as response:
            # Validate the socket before status/body processing.  The request
            # keeps its original hostname, so HTTPS certificate checks and SNI
            # are not weakened by IP-address URL rewriting.
            _validate_connected_peer(response)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                raise ValueError(f"Card source returned non-image content type {content_type!r}")
            declared = response.headers.get("content-length")
            if declared and int(declared) > 15 * 1024 * 1024:
                raise ValueError("Card source exceeds the 15 MiB safety limit")
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > 15 * 1024 * 1024:
                    raise ValueError("Card source exceeds the 15 MiB safety limit")
                content.extend(chunk)
    return bytes(content)


def _safe_output_path(output_dir: Path, filename: str) -> Path:
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Photo agent returned an empty output filename")
    relative = Path(filename)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name != filename:
        raise ValueError(f"Unsafe photo agent output filename: {filename!r}")
    candidate = (output_dir / relative).resolve()
    try:
        candidate.relative_to(output_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Photo agent output escapes output_dir: {filename!r}") from exc
    return candidate


def _copy_atomic(source: Path, destination_dir: Path, key: str) -> Path:
    """Validate, stage, revalidate and atomically publish photo-agent output."""
    expected_extension = card_image_extension(source, require_matching_suffix=False)
    destination_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{key}.", suffix=".tmp", dir=destination_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as source_stream:
            copied = 0
            while chunk := source_stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_CARD_BYTES:
                    raise ValueError(f"Generated card is unexpectedly large: {source}")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        actual_extension = card_image_extension(
            temporary, require_matching_suffix=False
        )
        if actual_extension != expected_extension:
            raise ValueError("Generated card format changed while copying")
        destination = destination_dir / f"{key}{actual_extension}"
        os.replace(temporary, destination)
        destination.chmod(0o644)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def run_once(groups, cfg: FotogenConfig, store: CardJobStore,
             http: httpx.Client | None = None, fetch_photo=None,
             manual_brief: dict | None = None) -> tuple[int, int]:
    """Один проход. Возвращает (submitted, published)."""
    cards = Path(cfg.cards_dir)
    cards.mkdir(parents=True, exist_ok=True)
    out_dir = Path(cfg.output_dir)
    fetch_photo = fetch_photo or _http_get
    submitted = published = 0

    # 1) забрать готовые
    pend = store.pending()
    if pend:
        in2key = {f: k for k, f in pend}
        for in_fn, out_fn in done_results(cfg.queue_db, list(in2key)).items():
            src = _safe_output_path(out_dir, out_fn)
            if src.exists():
                _copy_atomic(src, cards, in2key[in_fn])
                store.record(in2key[in_fn], in_fn, "done")
                published += 1
        for in_fn in failed_inputs(cfg.queue_db, list(in2key)):
            key = in2key[in_fn]
            prev = store.get(key)
            store.record(key, in_fn, "failed", tries=(prev[2] if prev else 1))   # сохраняем счётчик попыток

    # 2) поставить новые (серии без карточки), с потолком «в работе» И общим лимитом max_total
    outstanding = len(store.pending())
    total_slots = max(0, cfg.max_total - store.count())   # глобальный лимит карточек (тест ~20)
    budget = max(0, min(cfg.per_run, cfg.max_pending - outstanding, total_slots))
    for g in groups:
        if submitted >= budget:
            break
        key = card_key(g.supplier_sku)
        if existing_card_path(g.supplier_sku, cards, [".jpg", ".jpeg", ".png"]):
            continue
        legacy_key = legacy_card_key(g.supplier_sku)
        st = store.get(key) or (store.get(legacy_key) if legacy_key != key else None)
        next_tries = 1
        if st:
            status, tries = st[1], st[2]
            if status in ("pending", "done"):
                continue
            if status == "failed" and tries >= MAX_TRIES:
                continue                       # исчерпали попытки — сдаёмся (не долбим агента)
            next_tries = tries + 1             # failed с запасом попыток → переотправляем
        rep = g.representative
        photo_url = card_input_photo(rep)          # кадр внутреннего блока (герой карточки)
        if not photo_url:
            continue
        mode = (cfg.modes or {}).get(getattr(g, "key", None)) or cfg.mode
        rep_nc = rep.supplier_sku.split(":", 1)[-1]
        brief = (manual_brief or {}).get(rep_nc) or card_brief(g)   # ручное УТП переопределяет авто
        try:
            in_fn = submit_card_job(cfg, fetch_photo(photo_url), g.brand,
                                    f"{g.brand} {g.series}".strip(), brief,
                                    http=http, mode=mode)
        except Exception as e:
            # Раньше ошибка сети/API проглатывалась молча — теперь виден ключ серии и причина
            # (важно для GUI «Контент-студии», где вывод cards_run показывается пользователю).
            print(f"card submit failed for {getattr(g, 'key', key)}: {e}")
            continue
        if in_fn:
            store.record(key, in_fn, "pending", tries=next_tries)
            submitted += 1

    # Есть незавершённые задачи → будим локального агента (WatchDog поднимет Chrome+агента).
    if store.pending():
        try:
            wake_agent(cfg.queue_db)
        except Exception as exc:
            print(f"card agent wake failed: {exc}")
    return submitted, published
