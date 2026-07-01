# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Avito Bridge — автопубликация товаров (кондиционеры) на Avito через Автозагрузку (XML-фид) + Avito API.
Читает каталог из БД → цена → описание+карточка → per-series XML-фид → Avito забирает по расписанию.
Комментарии и контент — на русском; придерживайся этого стиля.

## Команды

```bash
python -m pip install -e ".[dev]"          # установка (pytest в extras dev)
python -m pytest -q                         # ВСЕ тесты (pyproject: pythonpath=src, testpaths=tests)
python -m pytest tests/test_render.py -q     # один файл
python -m pytest tests/test_render.py::test_smart_title_fixes_caps -q   # один тест
python -m avito_bridge                       # один цикл: собрать фид → feed_out/feed.xml
```
- **psycopg2 нужен только для боевого пути** (чтение БД oasis). Юнит-тесты БД НЕ трогают — сеть/БД
  замоканы (httpx.MockTransport, sqlite in-memory). Держи тесты офлайн.
- **Генерация карточек** (мост к фотоагенту, требует FOTOGEN_* в .env):
  `PYTHONPATH=src python -m avito_bridge.cards_run`

## Где это РАБОТАЕТ (важно для ментальной модели)
- Разработка — локально; **боевой прогон — на VPS 213.109.202.45** (`/opt/avito-bridge`, venv, systemd).
  Таймеры: `avito-bridge.timer` (сборка фида, ~ежечасно), `avito-cards.timer` (карточки, ~2ч). Фид
  отдаётся nginx: `/opt/oasis/staticfiles/avito-feed.xml` → `https://splithome.ru/static/avito-feed.xml`.
- **БД oasis — read-only** (контейнер на том же VPS). **Фотоагент — на ЛОКАЛЬНОМ ПК** владельца
  (Chrome+веб-ChatGPT), поднимается WatchDog'ом по флагу в очереди. Т.е. фид/цены обновляются без ноута,
  а НОВЫЕ карточки-картинки требуют включённого ноута.
- **Деплой:** `scp на этом VPS не работает`. Схема: `tar -czf … src config && ssh … 'cat > /tmp/x.tgz' <
  x.tgz && ssh … 'cd /opt/avito-bridge && tar -xzf /tmp/x.tgz'`. SSH-ключ `~/.ssh/climat_simf_deploy`.

## Архитектура (поток данных)
Один проход = `orchestrator/pipeline.py::run_cycle`:
1. **ingest** (`ingest/`): `oasis_db.fetch_raw_products` (БД oasis: товары+ТТХ+**мощность охлаждения кВт**,
   склад «Симферополь») + `jac_json` → `normalize.to_offer` → `Offer`. Здесь же `force_include`
   (товары под заказ, минуя наличие, с ручной ценой) и `manual_photos` (фото товарам без картинки в БД).
2. **catalog/series.py::group_by_series**: модели схлопываются в СЕРИИ (одно объявление на серию, а не на
   типоразмер). `clean_series` сливает близнецы (R32/год-версии), даёт короткое имя. Репрезентативная
   модель = младший размер (стабильный ad_id/имя карточки).
3. **pricing/pricing.py::compute_price**: `опт × (1+markup%) → округление вверх до …90` (`price_override`
   для force_include).
4. **content/**: `render.py::render_series` (заголовок+таблица «типоразмер→цена»+продающий текст, БЕЗ LLM),
   `descriptions.py` (ручные тексты из `avito-descriptions/manifest.json` переопределяют автотекст),
   `cards.py::resolve_photos` (уникальная карточка вместо фото поставщика), `sizing.py`.
5. **feed/builder.py**: `Offer`→`AdRecord`→Avito Autoload XML (`feed/writer.py` — атомарная запись).
6. Синхронизация/отчёты — `avito/client.py` (OAuth, `/autoload/v2/reports`).

Второй вход — **`cards_run.py`** (генерация карточек): `cards_pipeline.py` кладёт задачи в очередь
фотоагента (`submit_card_job`), будит агента (`wake_agent` — флаг в SQLite), забирает готовые в папку
`avito-cards/`. `CardJobStore` (state) — маппинг серия→задача + авто-ретрай (`tries`, MAX_TRIES=3).

## Ключевые не-очевидные моменты
- **Курирование через `config/config.yaml`** — это пульт: `catalog.selected_series` (whitelist серий),
  `catalog.force_include` `{nc:{price,series}}`, `catalog.manual_photos` `{nc:url}`,
  `cards.supplier_photo_series` (публикуются на фото поставщика, БЕЗ генер-карточки),
  `cards.require_for_publish` (без карточки не публикуем → нет блока «дубль фото»), `content.descriptions_manifest`.
- **Размер (7/9/12/18/24) берётся из мощности охлаждения кВт** (`sizing.derive_size` / `size_from_kw`), НЕ из
  `btu_calc` (он недостоверен). Карта площадей отключена. См. `ingest/oasis_db.COOL_KW_QUERY`.
- **Заголовок Avito**: `render._smart_title` (длинный КАПС→Капс, иначе Avito делает строчным),
  `_header_type`/`_fit_title` (без «Настенная», инвертор впереди, площадь у модели, ≤50 симв.).
- **Avito кэширует картинки по URL** → `resolve_photos` добавляет `?v=mtime`, иначе перегенеренная карточка
  не перекачивается.
- **`.gitignore` имеет широкие `*.txt`/`*.json`** — контент-файлы (`avito-descriptions/`, `config/card_modes.json`,
  `inbox/*.yaml`) добавлены через негейты-исключения. Новый контент проверяй `git status`, при нужде негейт.
- **Инбокс** (`inbox/` + `scripts/apply_inbox.py` + `inbox/README.md`) — как единым проходом завести товар
  (фото+текст+цена→публикация). Бэкенд для будущей GUI-студии (см. память project_avito_content_studio).
- **Секреты в `.env`** (gitignored): DB, `FOTOGEN_*`, Avito-ключи автозагрузки. На сервере креды БД —
  server-side, не эхоить.
