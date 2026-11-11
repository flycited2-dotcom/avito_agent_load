# Сохраняемый сборщик АРУ через CUA

`scripts/aru_collect_cua.mjs` — единственный браузерный оркестратор полного
обхода. Он использует существующий DOM reader `scripts/aru_account_dom.js`
и три поддерживаемые CUA-вкладки одного авторизованного профиля. HTTP API,
cookies, токены и коды входа не сохраняются. Из обычного Node CLI сайт не
загружается: запускать модуль внутри CUA REPL после чтения computer-use.

## Запуск и продолжение

Сначала проверить вход и переключить сайт в список через UI. Создать три
вкладки в том же выбранном браузере. Для нового прохода первая вкладка
должна показывать каталог с меню. Пример после получения этих handles:

```javascript
var collectorModule = await import('file:///C:/Users/TLT-1/Documents/GitHub/Codex/Avito/avito-bridge/scripts/aru_collect_cua.mjs');
var collector = await collectorModule.openCollector({
  tabs: [catalogTab, workerTabA, workerTabB],
  outputDir: 'C:/Users/TLT-1/Documents/GitHub/Codex/Avito/outputs/aru-refresh-YYYYMMDD-HHMM',
  readerPath: 'C:/Users/TLT-1/Documents/GitHub/Codex/Avito/avito-bridge/scripts/aru_account_dom.js',
  resume: false
});
nodeRepl.write(await collector.batch(36));
```

Повторять `batch(36)` короткими вызовами (таймаут инструмента 60 секунд).
После каждой страницы сохраняются JSON наблюдения, очередь в
`scan-progress.json` и `categories-menu.json`. Модуль добавляет родителей,
детей, канонические префиксы карточек и всю наблюдаемую пагинацию; включает
`/vse-tovary/` и `/novinki/`. Сохраняются также отсутствие и под заказ.

Для продолжения открыть тот же каталог результатов с `resume: true`.
Модуль проверит существование, вход, узел, номер и возраст сохранённых
страниц. При таймауте сохранить имеющийся прогресс, создать свежие вкладки
того же профиля на `status().next.url`, проверить вход и снова вызвать
`openCollector` с новыми handles и `resume: true`. Автоматического
перезапуска вкладок модуль не делает. Если вход сохранился, новый код не нужен.

`status: error`/`mismatch` требует устранения причины; страница не считается
завершённой. Не использовать предыдущую неделю или подменять timestamps.
Полнота подтверждается только компилятором после `remaining: 0`, включая
проверку свежести всех страниц и согласованности пагинации.

## Проверки и компиляция

```text
node --test tests/test_aru_collect_cua.mjs
python scripts/refresh_aru_account.py --category-crawl --categories <run>/categories-menu.json --pages-dir <run>/pages --output-dir <run>/compiled --compile-only
```

Правила цены, дерева, сравнения и согласования установки находятся в
`ARU_WEEKLY_REFRESH.md`. Сборщик не меняет рабочие прайсы, выборы и фиды.
Установку согласованного файла выполнять отдельно через существующий
`install_aru_snapshot.py`, с SHA-барьером и резервными копиями.

## Наблюдение о времени сбора

10.11.2026 владелец отметил, что во вторник вечером обход заметно быстрее.
Это наблюдение записано для будущего выбора окна сбора, без гарантии
скорости. Понедельничное расписание этим не изменяется.
