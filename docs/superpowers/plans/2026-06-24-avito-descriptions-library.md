# Библиотека описаний Avito — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать проверяемую библиотеку из 20 коротких, отличающихся друг от друга описаний для актуальных серий кондиционеров в Avito.

**Architecture:** Новый каталог `avito-descriptions/` остаётся независимым от Python-генератора фида. Двадцать `.txt`-файлов хранят готовые для вставки тексты, а `README.md` однозначно связывает каждый файл с объявлением текущего фида и подтверждёнными параметрами. Цены не дублируются, потому что остаются динамическими в XML-фиде.

**Tech Stack:** UTF-8 plain text, Markdown, PowerShell для проверки покрытия и длины текстов.

> **Актуализация 24.06.2026:** после расширения тестовой партии текущий фид содержит 35 активных серий. Финальная библиотека заменяет снятую с фида серию EXPERTAIR by ZILON HARD и содержит 35 текстов; актуальная карта файлов находится в `avito-descriptions/README.md`.

---

## File structure

- Create: `avito-descriptions/README.md` — карта файлов, серий и параметров снимка фида от 2026-06-24.
- Create: `avito-descriptions/01-alfacool-apus.txt` … `avito-descriptions/20-funai-sensei-2025.txt` — по одному готовому описанию на объявление.
- Do not modify: `src/avito_bridge/content/render.py`, `config/config.yaml` и текущий генератор фида.

### Task 1: Создать навигацию по библиотеке

**Files:**
- Create: `avito-descriptions/README.md`

- [ ] **Step 1: Создать каталог `avito-descriptions/` и README в UTF-8.**

- [ ] **Step 2: Внести таблицу из 20 строк со столбцами `№`, `Файл`, `Серия`, `ID объявления`, `Типоразмеры в снимке`.**

  Использовать эти точные соответствия:

  | № | Файл | Серия | ID | Типоразмеры |
  |---|---|---|---|---|
  | 01 | `01-alfacool-apus.txt` | ALFACOOL APUS | `a733baf6bbe47d8d77e6fb71` | 24 тыс. BTU, до 70 м² |
  | 02 | `02-expertair-cyclone-dc-inverter.txt` | EXPERTAIR by ZILON CYCLONE DC Inverter | `baf96c8a4fedb1b226313ac0` | 7–22 тыс. BTU, до 20–60 м² |
  | 03 | `03-expertair-proff-dc-inverter.txt` | EXPERTAIR by ZILON PROFF DC Inverter | `48e35ba8c4b74225bf2d3efd` | 7 тыс. BTU, до 20 м² |
  | 04 | `04-expertair-hard.txt` | EXPERTAIR by ZILON HARD | `68a52facd20e671875830dac` | 36 тыс. BTU, до 100 м² |
  | 05 | `05-expertair-progress.txt` | EXPERTAIR by ZILON PROGRESS | `b1f33002692e4df5f73e781f` | 12–25 тыс. BTU |
  | 06 | `06-funai-akoya-inverter.txt` | FUNAI AKOYA Inverter | `1ee314ccee00d6e309b9b761` | 7–26 тыс. BTU, до 20–75 м² |
  | 07 | `07-funai-akoya-nero-inverter.txt` | FUNAI AKOYA NERO Inverter | `1fded23574989b87f208cf23` | 12 тыс. BTU, до 35 м² |
  | 08 | `08-funai-daijin-inverter.txt` | FUNAI DAIJIN Inverter | `4e74eddf063c4313ea63fb18` | 7 тыс. BTU, до 20 м² |
  | 09 | `09-funai-emperor-smart-eye.txt` | FUNAI EMPEROR SMART EYE FULL DC Inverter 2024 | `10da15ca01a1ae15475a690b` | 9 тыс. BTU, до 25 м² |
  | 10 | `10-funai-emperor-up-smart-eye.txt` | FUNAI EMPEROR UP SMART EYE FULL DC Inverter | `0d6d9425e7cff84298819c25` | 12 тыс. BTU, до 35 м² |
  | 11 | `11-funai-kadzoku-inverter-2024.txt` | FUNAI KADZOKU Inverter 2024 | `54598db9144e6012c8e87f9b` | 7 тыс. BTU, до 20 м² |
  | 12 | `12-funai-kagami-inverter.txt` | FUNAI KAGAMI Inverter | `2aee5fbf4f171c284d2764d2` | 10 тыс. BTU, до 28 м² |
  | 13 | `13-funai-shogun-inverter-2024.txt` | FUNAI SHOGUN Inverter 2024 | `5c749ca945b64210c663d331` | 18 тыс. BTU, до 50 м² |
  | 14 | `14-funai-sensei-2-inverter.txt` | FUNAI SENSEI 2.0 Inverter | `31e2cf25d7eea8e194c9fc6b` | 7–12 тыс. BTU, до 20–35 м² |
  | 15 | `15-funai-daijin.txt` | FUNAI DAIJIN | `85a2caff72b06ae982dc8929` | 20 тыс. BTU, до 55 м² |
  | 16 | `16-funai-kadzoku-2025.txt` | FUNAI KADZOKU 2025 | `690242bbfa749d75ef867620` | 10–25 тыс. BTU |
  | 17 | `17-funai-kagami.txt` | FUNAI KAGAMI | `6ccc80ef6090498fe29133f0` | 7–12 тыс. BTU, до 20–35 м² |
  | 18 | `18-funai-sensei-2.txt` | FUNAI SENSEI 2.0 | `0fc2d07d0ec03e4a37a7abf6` | 7–9 тыс. BTU, до 20–25 м² |
  | 19 | `19-funai-kadzoku-inverter-2025.txt` | FUNAI KADZOKU Inverter 2025 | `de82fe9cd9173e849549ebb4` | 13–25 тыс. BTU |
  | 20 | `20-funai-sensei-2025.txt` | FUNAI SENSEI 2025 | `608a1cee83d53b3c6210ac2b` | 18 тыс. BTU, до 50 м² |

- [ ] **Step 3: Добавить правило обновления.**

  В README явно указать: тексты созданы по снимку фида от 2026-06-24; перед добавлением новой серии необходимо обновить таблицу, но цену в `.txt` не вставлять.

- [ ] **Step 4: Проверить README.**

  Run: `rg -n '^\| [0-9]{2} \|' avito-descriptions/README.md | Measure-Object`

  Expected: `Count : 20`.

### Task 2: Написать описания ALFACOOL и EXPERTAIR by ZILON

**Files:**
- Create: `avito-descriptions/01-alfacool-apus.txt`
- Create: `avito-descriptions/02-expertair-cyclone-dc-inverter.txt`
- Create: `avito-descriptions/03-expertair-proff-dc-inverter.txt`
- Create: `avito-descriptions/04-expertair-hard.txt`
- Create: `avito-descriptions/05-expertair-progress.txt`

- [ ] **Step 1: Создать пять текстов в UTF-8 без заголовков и цен.**

  Отразить только эти подтверждённые отличия: APUS — настенная неинверторная серия 24 тыс. BTU; CYCLONE — инверторная серия 7–22 тыс. BTU; PROFF — инверторная «семёрка»; HARD — кассетный неинверторный полупромышленный кондиционер 36 тыс. BTU; PROGRESS — настенная неинверторная серия 12–25 тыс. BTU.

- [ ] **Step 2: Сделать заходы разными.**

  Для APUS — просторная гостиная или дом; CYCLONE — выбор мощности в одной линейке; PROFF — небольшая комната; HARD — офис, зал или коммерческое помещение; PROGRESS — выбор между несколькими площадями. Не утверждать неподтверждённые режимы и функции.

- [ ] **Step 3: Завершить каждый текст разной формой призыва написать площадь, город и особенности помещения.**

### Task 3: Написать инверторные описания FUNAI

**Files:**
- Create: `avito-descriptions/06-funai-akoya-inverter.txt`
- Create: `avito-descriptions/07-funai-akoya-nero-inverter.txt`
- Create: `avito-descriptions/08-funai-daijin-inverter.txt`
- Create: `avito-descriptions/09-funai-emperor-smart-eye.txt`
- Create: `avito-descriptions/10-funai-emperor-up-smart-eye.txt`
- Create: `avito-descriptions/11-funai-kadzoku-inverter-2024.txt`
- Create: `avito-descriptions/12-funai-kagami-inverter.txt`
- Create: `avito-descriptions/13-funai-shogun-inverter-2024.txt`
- Create: `avito-descriptions/14-funai-sensei-2-inverter.txt`
- Create: `avito-descriptions/19-funai-kadzoku-inverter-2025.txt`

- [ ] **Step 1: Создать десять инверторных текстов в UTF-8 без заголовков и цен.**

  В каждом разрешено говорить только о подтверждённой инверторной технологии и диапазоне серии: AKOYA 7–26, AKOYA NERO 12, DAIJIN 7, EMPEROR SMART EYE 9, EMPEROR UP SMART EYE 12, KADZOKU 2024 7, KAGAMI 10, SHOGUN 18, SENSEI 2.0 7–12, KADZOKU 2025 13–25 тыс. BTU.

- [ ] **Step 2: Не превращать название серии в техническое обещание.**

  В частности, слова `SMART EYE`, `FULL DC`, `NERO`, `KAGAMI`, `SHOGUN` и `KADZOKU` использовать как название линейки, но не выводить из них сенсоры, Wi-Fi, цвет, уровень шума или особые режимы.

- [ ] **Step 3: Варьировать сценарии выбора.**

  Использовать разные ситуации: спальня или кабинет; квартира; помещение с теплопритоками; большая комната; помощь в подборе мощности. Общее обещание продавца ограничить новым товаром, гарантией производителя, доставкой по Крыму и помощью с монтажом.

### Task 4: Написать классические описания FUNAI

**Files:**
- Create: `avito-descriptions/15-funai-daijin.txt`
- Create: `avito-descriptions/16-funai-kadzoku-2025.txt`
- Create: `avito-descriptions/17-funai-kagami.txt`
- Create: `avito-descriptions/18-funai-sensei-2.txt`
- Create: `avito-descriptions/20-funai-sensei-2025.txt`

- [ ] **Step 1: Создать пять неинверторных настенных описаний в UTF-8 без заголовков и цен.**

  Факты для текстов: DAIJIN — 20 тыс. BTU, до 55 м²; KADZOKU 2025 — 10–25 тыс. BTU; KAGAMI — 7–12 тыс. BTU, до 20–35 м²; SENSEI 2.0 — 7–9 тыс. BTU, до 20–25 м²; SENSEI 2025 — 18 тыс. BTU, до 50 м².

- [ ] **Step 2: Избежать повторов инверторных текстов.**

  Подать их как понятный выбор под реальную площадь и бюджет, не использовать слова «инвертор», «тихая работа» или «экономия электроэнергии» как преимущества этих серий.

- [ ] **Step 3: Закончить каждый файл самостоятельным призывом написать продавцу.**

### Task 5: Проверить библиотеку и зафиксировать результат

**Files:**
- Verify: `avito-descriptions/README.md`
- Verify: `avito-descriptions/*.txt`

- [ ] **Step 1: Проверить количество, длину и призыв в каждом тексте.**

  Run:

  ```powershell
  $files = Get-ChildItem avito-descriptions -Filter *.txt
  if ($files.Count -ne 20) { throw "Expected 20 descriptions, got $($files.Count)" }
  $files | ForEach-Object {
    $text = Get-Content -Raw $_.FullName
    if ($text.Length -lt 500 -or $text.Length -gt 900) { throw "$($_.Name): $($text.Length) chars" }
    if ($text -notmatch 'Напишите') { throw "$($_.Name): CTA missing" }
  }
  'Descriptions: OK'
  ```

  Expected: `Descriptions: OK`.

- [ ] **Step 2: Проверить отсутствие цен, запрещённого слова и точных дублей.**

  Run:

  ```powershell
  $files = Get-ChildItem avito-descriptions -Filter *.txt
  if (Select-String -Path $files.FullName -Pattern '₽|скидка только сегодня|звоните') { throw 'Forbidden sales phrase or price found' }
  $hashes = $files | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
  if (($hashes | Select-Object -Unique).Count -ne 20) { throw 'Duplicate descriptions found' }
  'Content safety: OK'
  ```

  Expected: `Content safety: OK`.

- [ ] **Step 3: Сверить, что таблица README содержит все 20 имён файлов.**

  Run:

  ```powershell
  $readme = Get-Content -Raw avito-descriptions/README.md
  Get-ChildItem avito-descriptions -Filter *.txt | ForEach-Object {
    if (-not $readme.Contains($_.Name)) { throw "README is missing $($_.Name)" }
  }
  'README coverage: OK'
  ```

  Expected: `README coverage: OK`.

- [ ] **Step 4: Провести ручную редактуру.**

  Проверить каждый файл на соответствие серии, естественный русский язык, отсутствие технических выдумок и различие первого абзаца и CTA.

- [ ] **Step 5: Commit.**

  ```bash
  git add avito-descriptions docs/superpowers/plans/2026-06-24-avito-descriptions-library.md
  git commit -m "docs: add Avito descriptions library"
  ```
