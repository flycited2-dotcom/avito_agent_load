# Avito Bridge

Автоматическая публикация и синхронизация товарных объявлений (кондиционеры) на Avito через
Автозагрузку (XML-фид) и Avito API.

- **ТЗ:** [docs/TZ-Avito-Bridge.md](../docs/TZ-Avito-Bridge.md) (полный контекст, источники, интеграция)
- **План Фазы 1 (MVP):** [docs/superpowers/plans/2026-06-22-avito-bridge-phase1-mvp.md](../docs/superpowers/plans/2026-06-22-avito-bridge-phase1-mvp.md)

## Что делает (Фаза 1, MVP)

Читает каталог (БД SplitHome «oasis» + JAC JSON) → опт по поставщикам → цена `опт×1.05 → …90` →
контент-шаблон → Avito Autoload XML с фан-аутом по городам → атомарная запись фида (раздаётся nginx) →
синхронизация через Avito API (OAuth, профиль, прогон, отчёты).

## Установка (разработка)

```bash
python -m pip install -e ".[dev]"   # либо: pip install pydantic httpx lxml python-decouple PyYAML pytest
cp .env.example .env                 # заполнить креды (см. Фаза 0 в плане)
python -m pytest -q                  # все тесты должны быть зелёными
```

> `psycopg2-binary` нужен только для боевого пути (чтение БД); ставится на сервере (Python 3.13).

## Запуск (боевой)

```bash
python -m avito_bridge                # один цикл: собрать фид → feed_out/feed.xml
```

## Деплой (VPS 213.109.202.45)

См. `deploy/`: `avito-bridge.service` + `avito-bridge.timer` (каждые ~3.5 ч), `nginx-feed.conf`
(раздача фида). Перед публикацией: получить `category fields` целевой категории Avito и заполнить
теги в `config/config.yaml` (`feed.base_tags`). Правки nginx — с бэкапом и `nginx -t`.

## Структура

`src/avito_bridge/`: `ingest/` (oasis_db, jac_json, opt_resolver, normalize), `catalog/`, `pricing/`,
`content/` (sizing, render), `feed/` (ad_id, builder, writer), `avito/` (client), `state/`,
`orchestrator/` (pipeline), `config.py`, `models.py`.

## Статус

Фаза 1 (MVP) — код и юнит-тесты готовы (offline). Осталось (Фаза 0, требует доступов):
живой дымовой прогон против Avito API + БД, `category fields`, настройка Автозагрузки.
Фазы 2–3 (Breez-опт API, LLM-контент, Telegram-алерты, быстрый путь цен, фотоагент) — отдельные планы.
