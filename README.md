# Подготовка датасета для ML

Веб-сервис на **FastAPI** и **Claude (Anthropic)**: вы загружаете табличный файл (CSV / Excel), выбираете **пресет** под тип модели, агент строит план очистки, валидации и биннинга; план **детерминированно** применяется в **pandas** без повторного вызова LLM на каждую строку. На выходе — готовый CSV, JSON-план, ZIP-архив и опционально **Word-отчёт** с метриками биннинга.

---

## Содержание

- [Как это работает](#как-это-работает)
- [Возможности](#возможности)
- [Требования](#требования)
- [Установка](#установка)
- [Конфигурация](#конфигурация)
- [Запуск](#запуск)
- [Публикация по ссылке (хостинг)](#публикация-по-ссылке-хостинг)
- [Веб-интерфейс](#веб-интерфейс)
- [Пресеты](#пресеты)
- [Схема плана обработки (JSON)](#схема-плана-обработки-json)
- [Что делает исполнитель](#что-делает-исполнитель)
- [Форматы результата](#форматы-результата)
- [Word-отчёт и метрики биннинга](#word-отчёт-и-метрики-биннинга)
- [Чат с агентом](#чат-с-агентом)
- [API](#api)
- [Структура проекта](#структура-проекта)
- [Ограничения и безопасность](#ограничения-и-безопасность)
- [Устранение неполадок](#устранение-неполадок)
- [Лицензия](#лицензия)

---

## Как это работает

```
┌─────────────┐     загрузка      ┌──────────────────┐
│  CSV/XLSX   │ ────────────────► │  FastAPI (main)  │
└─────────────┘                   └────────┬─────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
              ▼                          ▼                          ▼
     POST /preview-dataset      POST /prepare-dataset        POST /chat
     (только pandas)             (Claude → JSON-план)         (Claude + контекст)
              │                          │
              │                          ▼
              │                 ┌──────────────────┐
              │                 │  executor.py     │
              │                 │  apply_plan()    │
              │                 └────────┬─────────┘
              │                          │
              ▼                          ▼
        JSON превью              CSV / ZIP / JSON-план
```

1. **Превью** (`/preview-dataset`) — чтение файла в pandas, статистика по колонкам и первые строки. LLM не вызывается.
2. **Обработка** (`/prepare-dataset`) — первые 50 строк и метаданные отправляются в Claude; модель возвращает JSON по схеме `AnalystReport` (`schemas.py`).
3. **Исполнение** — `executor.py` применяет план к **полному** датафрейму: те же правила для всех строк, результат воспроизводим.
4. **Чат** — отдельный вызов Claude с историей диалога и компактным контекстом последнего запуска (план, заметки, журнал; без сырых строк таблицы).

Пресет влияет только на **текст промпта** планировщика (`domain_presets.py`), а не на отдельный код обработки: для всех доменов один исполнитель.

---

## Возможности

| Функция | Описание |
|--------|----------|
| Превью данных | Сразу после выбора файла: число строк/колонок, типы, доли пропусков, первые 1–20 строк |
| Планирование | Claude проектирует очистку, валидацию и биннинг с учётом домена (антрифрод, скоринг и т.д.) |
| Детерминированное исполнение | Дедупликация, fill NA, IQR, приведение типов, clip, биннинг — только pandas |
| Биннинг | Колонка `*_binned`: номера групп **1, 2, 3, …** (не интервалы `(653.5, 682.0]`) |
| Пресеты | Разные инструкции для планировщика под тип ML-задачи |
| Режим «только план» | JSON без изменения данных (`preview_only=true`) |
| ZIP-архив | `prepared.csv` + `report.json` |
| Word-отчёт | Графики, WoE, IV, KS, Gini — если найден таргет |
| Чат | Вопросы по последнему плану и журналу шагов |

---

## Требования

- **Python 3.10+**
- Ключ **Anthropic API** (`ANTHROPIC_API_KEY`)
- Доступ в интернет к API Anthropic при обработке и в чате
- Поддерживаемые форматы файлов: **`.csv`**, **`.xlsx`**, **`.xlsm`**

Зависимости перечислены в `requirements.txt`: FastAPI, uvicorn, pandas, openpyxl, anthropic, python-dotenv, python-docx, matplotlib.

---

## Установка

### Windows (PowerShell)

```powershell
cd c:\Users\NSapargaliyeva\claude
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux / macOS

```bash
cd /path/to/claude
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Конфигурация

Создайте файл **`.env`** в корне проекта (не коммитьте в git):

```env
# Обязательно
ANTHROPIC_API_KEY=sk-ant-...

# Необязательно — модель для планировщика и чата (по умолчанию claude-haiku-4-5-20251001)
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

Переменные читаются через `python-dotenv` при импорте `app.llm`. После изменения `.env` перезапустите uvicorn.

| Переменная | Назначение |
|------------|------------|
| `ANTHROPIC_API_KEY` | Ключ API Anthropic |
| `ANTHROPIC_MODEL` | Идентификатор модели Claude для `/prepare-dataset` и `/chat` |

---

## Запуск

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

| URL | Назначение |
|-----|------------|
| http://127.0.0.1:8000/ | Веб-интерфейс |
| http://127.0.0.1:8000/docs | Swagger UI (интерактивная документация API) |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/health | Проверка, что сервис поднят |

Для продакшена уберите `--reload` и при необходимости смените `host` / `port` / прокси.

---

## Публикация по ссылке (хостинг)

### Вариант 1 — локальная сеть (быстро, без облака)

Доступ с других компьютеров в той же Wi‑Fi/LAN:

```powershell
cd c:\Users\NSapargaliyeva\claude
.\run-public.ps1
```

Или без скрипта:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Откройте ссылку вида `http://192.168.x.x:8000` (IP покажет скрипт или `ipconfig`).  
**Важно:** файрвол Windows может запросить разрешение для Python — разрешите входящие подключения.

### Вариант 2 — публичная ссылка в интернете (Render.com)

В репозитории есть `Dockerfile` и `render.yaml`.

1. Залейте проект на **GitHub** (без `.env` и `.venv` — они в `.gitignore` / `.dockerignore`).
2. Зарегистрируйтесь на [https://render.com](https://render.com).
3. **New → Blueprint** → подключите репозиторий → Render подхватит `render.yaml`.
4. В настройках сервиса добавьте переменную окружения **`ANTHROPIC_API_KEY`**.
5. После деплоя получите URL: `https://dataset-prep-agent-xxxx.onrender.com`.

Проверка: `https://ваш-домен.onrender.com/health` → `{"status":"ok"}`.

На бесплатном плане сервис «засыпает» без трафика ~15 мин — первый заход может быть медленным.

### Вариант 3 — Docker на своём сервере / VPS

```bash
docker build -t dataset-prep .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... dataset-prep
```

Ссылка: `http://IP_сервера:8000`. Для HTTPS поставьте перед приложением **nginx** или **Caddy** с Let's Encrypt.

### Вариант 4 — временная ссылка (ngrok)

Если сервис уже запущен на `127.0.0.1:8000`:

```powershell
ngrok http 8000
```

Ngrok выдаст временный URL `https://xxxx.ngrok-free.app` (нужна регистрация на ngrok.com).

---

## Веб-интерфейс

Файлы интерфейса: `app/static/index.html`, `app.js`, `app.css`.

### Пошаговый сценарий

1. В боковой панели выберите **тип модели** (пресет).
2. Загрузите **`.csv`**, **`.xlsx`** или **`.xlsm`** — появится блок **«Превью данных»** (запрос к `/preview-dataset`).
3. При необходимости отметьте опции:
   - **Только план (JSON)** — ответ без изменения таблицы; удобно проверить логику до полной обработки.
   - **ZIP** — в архиве `prepared.csv` и `report.json`.
   - **Отчёт Word** — доступен только вместе с ZIP; внутри архива будет `report.docx` (или `report_docx_error.txt` при ошибке сборки).
4. Нажмите **«Обработать»** — скачайте CSV или ZIP.
5. Внизу страницы — **чат с агентом**: контекст последнего запуска передаётся автоматически (заголовок `X-Prep-Context` от сервера).

### Заголовок `X-Prep-Context`

После `/prepare-dataset` сервер возвращает base64-кодированный JSON с планом, заметками и журналом. Клиент сохраняет его и подставляет в `POST /chat` как `processing_context`, чтобы ответы были привязаны к конкретному файлу и пресету.

---

## Пресеты

Параметр API и UI: `preset`. Меняет **доменный блок в промпте** и суффикс имени выходного файла: `{имя}_{preset}_prepared.csv`.

| ID | Название (RU) | Когда использовать |
|----|---------------|-------------------|
| `antifraud` | Антифрод | Транзакции, события, fraud / not fraud, риск утечек по времени |
| `legal_entity_scoring` | Скоринг юрлиц | PD / рейтинг компании, отчётность, ИНН/ОГРН, as-of дата |
| `individual_scoring` | Скоринг физлиц | Кредитные заявки, PD физлиц, бюро, доходы |
| `credit_transition_matrix` | Матрица переходов | Migration matrix рейтинговых корзин, wide/long формат |

### Что учитывает планировщик по пресетам (кратко)

**`antifraud`**
- Дисбаланс классов: осторожность с массовым `drop_iqr`.
- Поиск утечек (признаки «из будущего», статусы после события).
- Дедупликация по `transaction_id` / аналогам.
- Даты, velocity-признаки, категории (MCC, канал).
- Целевую колонку (`fraud_flag`, `is_fraud`) не удалять и не заполнять.

**`legal_entity_scoring`**
- Ключ юрлица + отчётная дата для dedupe.
- Финансовые показатели, ОКВЭД, регион.
- Предупреждения о lookup leakage относительно даты решения.
- Биннинг возраста компании, размера, коэффициентов.

**`individual_scoring`**
- Комплаенс: не использовать запрещённые признаки; только пометки в `validation_notes`.
- Возраст, доход, бюро-скоры, коды «нет данных» (-1, 999).
- Биннинг возраста, стажа, сумм; цель не трогать.

**`credit_transition_matrix`**
- Проверка неотрицательности и сумм по строкам ≈ 1.
- Не удалять столбцы-корзины как «лишние».
- Биннинг часто не нужен для уже дискретных корзин.

Список пресетов в API: `GET /file-presets`.

---

## Схема плана обработки (JSON)

Модель возвращает объект, валидируемый как `AnalystReport` (`app/schemas.py`). Пример (сокращённый):

```json
{
  "summary_ru": "Краткое резюме решений для этого файла и пресета.",
  "cleaning_notes": ["Заметка для аналитика без исполнения в коде"],
  "validation_notes": ["Риск утечки по колонке X"],
  "binning_notes": ["Имеет смысл биннить age и amount"],
  "cleaning": {
    "dedupe": "subset",
    "dedupe_columns": ["transaction_id"],
    "drop_columns": ["_unnamed"],
    "drop_rows_all_na": true,
    "fill_na": {
      "channel": { "strategy": "constant", "constant": "UNKNOWN" },
      "amount": { "strategy": "median" }
    },
    "numeric_outliers": {
      "columns": ["amount"],
      "mode": "winsorize_iqr",
      "iqr_multiplier": 1.5
    },
    "strip_whitespace": { "columns": null }
  },
  "validation": {
    "cast_columns": [
      { "column": "event_time", "dtype": "datetime64[ns]" },
      { "column": "amount", "dtype": "float64" }
    ],
    "numeric_clip": [
      { "column": "age", "min": 18, "max": 100 }
    ]
  },
  "binning": [
    {
      "column": "amount",
      "output_column": "amount_binned",
      "method": "quantile",
      "n_bins": 5,
      "labels": null
    }
  ]
}
```

### Поля `cleaning`

| Поле | Тип | Описание |
|------|-----|----------|
| `dedupe` | `none` \| `full` \| `subset` | Удаление дубликатов |
| `dedupe_columns` | `string[]` | Колонки для `subset` |
| `drop_columns` | `string[]` | Удалить колонки |
| `drop_rows_all_na` | `bool` | Удалить строки, где все значения NA |
| `fill_na` | `dict` | По колонке: `strategy` = `median` \| `mean` \| `mode` \| `constant` \| `drop_rows`; для `constant` — поле `constant` |
| `numeric_outliers` | объект \| `null` | `columns`, `mode`: `winsorize_iqr` \| `drop_iqr`, `iqr_multiplier` (по умолчанию 1.5) |
| `strip_whitespace` | объект \| `null` | `columns`: список или `null` = все строковые колонки |

### Поля `validation`

| Поле | Тип | Описание |
|------|-----|----------|
| `cast_columns` | массив | `column`, `dtype`: `int64`, `float64`, `string`, `category`, `datetime64[ns]`, `boolean` |
| `numeric_clip` | массив | `column`, `min`, `max` (опционально) |

### Поля `binning[]`

| Поле | Тип | Описание |
|------|-----|----------|
| `column` | string | Исходный числовой признак |
| `output_column` | string | Обычно `{column}_binned` |
| `method` | `quantile` \| `equal` | `pd.qcut` или `pd.cut` |
| `n_bins` | int (2–50) | Число интервалов |
| `labels` | `string[]` \| `null` | Если длина = числу интервалов — подписи категорий; иначе в CSV пишутся **1…K** |

Поля `*_notes` и `summary_ru` **не меняют данные** — только документируют решения для человека и для чата.

---

## Что делает исполнитель

Реализация: `app/executor.py`, функция `apply_plan(df, plan)`.

Порядок шагов:

1. **Очистка**
   - `dedupe` full / subset
   - удаление колонок и полностью пустых строк
   - `strip_whitespace` для object/string
   - выбросы по IQR: winsorize (clip) или удаление строк
   - заполнение пропусков или `drop_rows` по колонке
2. **Валидация**
   - приведение типов (в т.ч. nullable `Int64` для целых)
   - `numeric_clip` по min/max
3. **Биннинг**
   - `quantile` → `pd.qcut`, `equal` → `pd.cut`
   - при `labels=null` — целые **1…K** в `output_column`; пропуски в исходной колонке → NA в бинне

Возвращается `(prepared_dataframe, execution_log_ru)` — список человекочитаемых строк о каждом применённом шаге.

---

## Форматы результата

### Один CSV (по умолчанию)

- Имя: `{stem}_{preset}_prepared.csv`
- Кодировка UTF-8, без индекса
- Заголовок ответа: `Content-Disposition: attachment`

### Только план (`preview_only=true`)

- Тело: JSON плана + `preset_applied`
- `execution_log_ru` пустой
- В контексте чата `preview_plan_only: true`

### ZIP (`bundle=true`)

| Файл в архиве | Содержимое |
|---------------|------------|
| `prepared.csv` | Обработанная таблица |
| `report.json` | План + `execution_log_ru` + `preset_applied` |
| `report.docx` | При `docx_report=true` |
| `report_docx_error.txt` | Текст ошибки, если Word не собрался |

### `report.json`

Дублирует структуру плана и добавляет:

```json
{
  "summary_ru": "...",
  "cleaning": { ... },
  "validation": { ... },
  "binning": [ ... ],
  "execution_log_ru": [
    "Удалены полные дубликаты: 12 строк",
    "«amount»: winsorize IQR [100; 50000]"
  ],
  "preset_applied": "individual_scoring"
}
```

---

## Word-отчёт и метрики биннинга

Включается: `bundle=true` и `docx_report=true`. Сборка: `app/word_report.py`, метрики: `app/binning_metrics.py`.

Содержимое отчёта (типично):

- резюме плана (`summary_ru`);
- журнал шагов исполнения;
- графики долей пропусков;
- для каждого биннинга — таблица **WoE** и агрегаты **IV**, **KS**, **Gini** (при бинарном таргете).

### Распознавание таргета

Эвристика `guess_target_column()` ищет колонки с именами вроде:

`target`, `y`, `label`, `default`, `default_flag`, `is_fraud`, `fraud_flag`, `response`, `event`, а также подстроки `default`, `fraud`, `delinq`, `flag` и т.п.

Значения приводятся к бинарному виду (0 = хорошие, 1 = плохие). Если таргет не найден или не бинарный — метрики в Word могут быть ограничены или пропущены.

---

## Чат с агентом

**`POST /chat`** — отдельная сессия Claude с системным промптом ML-инженера (`AGENT_CHAT_SYSTEM` в `llm.py`).

Тело запроса:

```json
{
  "message": "Почему вы удалили дубликаты по transaction_id?",
  "history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "processing_context": { }
}
```

- `history` — до 40 сообщений, роли `user` | `assistant`.
- `processing_context` — опционально, до ~52 KB JSON; в веб-UI подставляется после обработки.
- В контекст **не попадают** сырые строки датасета — только план, заметки, имена колонок (до 60), счётчики строк.

Ошибки: `503` при отсутствии ключа API, `502` при сбое модели.

---

## API

### Сводная таблица

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Веб-интерфейс (`index.html`) |
| `GET` | `/health` | `{"status": "ok"}` |
| `GET` | `/file-presets` | Список `{id, label}` пресетов |
| `POST` | `/preview-dataset` | Превью без LLM |
| `POST` | `/prepare-dataset` | План + обработка |
| `POST` | `/chat` | Диалог с агентом |

Статические файлы: `/static/*`.

---

### `GET /health`

**Ответ 200:**

```json
{ "status": "ok" }
```

---

### `GET /file-presets`

**Ответ 200:** массив объектов `{ "id": "antifraud", "label": "Антифрод" }`, …

---

### `POST /preview-dataset`

**Query:** `sample_rows` — целое от 1 до 20 (по умолчанию 10).

**Тело:** `multipart/form-data`, поле `file`.

**Ответ 200 (пример):**

```json
{
  "filename": "data.csv",
  "row_count": 10000,
  "column_count": 15,
  "columns": ["id", "amount", "..."],
  "dtypes": { "amount": "float64" },
  "null_counts": { "amount": 0 },
  "null_rates": { "amount": 0.0 },
  "sample_rows": [ { "id": 1, "amount": 100.5 } ],
  "sample_row_count": 10
}
```

**Ошибки:** `400` — пустой файл, неподдерживаемый формат, ошибка чтения.

---

### `POST /prepare-dataset`

**Query-параметры:**

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `preset` | enum | `antifraud` | `antifraud`, `legal_entity_scoring`, `individual_scoring`, `credit_transition_matrix` |
| `preview_only` | bool | `false` | Только JSON-план |
| `bundle` | bool | `false` | ZIP вместо одного CSV |
| `docx_report` | bool | `false` | `report.docx` в ZIP; **только** при `bundle=true` |

**Тело:** `multipart/form-data`, поле `file`.

**Ответы:**

| Условие | Content-Type | Примечание |
|---------|--------------|------------|
| обычный режим | `text/csv` | Файл `{stem}_{preset}_prepared.csv` |
| `preview_only=true` | `application/json` | План без исполнения |
| `bundle=true` | `application/zip` | Архив с CSV и отчётами |

Заголовок **`X-Prep-Context`**: base64 JSON для чата (всегда при успешной обработке / preview_only).

**Примеры curl**

Только CSV:

```bash
curl -X POST "http://127.0.0.1:8000/prepare-dataset?preset=antifraud" \
  -F "file=@transactions.csv" \
  -o transactions_antifraud_prepared.csv
```

Только план:

```bash
curl -X POST "http://127.0.0.1:8000/prepare-dataset?preset=legal_entity_scoring&preview_only=true" \
  -F "file=@companies.xlsx"
```

ZIP + Word:

```bash
curl -X POST "http://127.0.0.1:8000/prepare-dataset?preset=individual_scoring&bundle=true&docx_report=true" \
  -F "file=@applications.csv" \
  -o result.zip
```

Превью:

```bash
curl -X POST "http://127.0.0.1:8000/preview-dataset?sample_rows=5" \
  -F "file=@data.csv"
```

**Ошибки:**

| Код | Причина |
|-----|---------|
| `400` | Пустой файл, `docx_report` без `bundle`, неподдерживаемый формат |
| `502` | Claude недоступен или JSON плана невалиден |
| `503` | Не задан `ANTHROPIC_API_KEY` (в чате) |

---

### `POST /chat`

**Тело:** JSON (`ChatRequest`).

**Ответ 200:**

```json
{ "reply": "Текст ответа агента на русском." }
```

**Пример:**

```bash
curl -X POST "http://127.0.0.1:8000/chat" \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"Какие колонки попали в биннинг?\", \"history\": []}"
```

---

## Структура проекта

```
claude/
├── app/
│   ├── main.py              # FastAPI: эндпоинты, загрузка файлов, ZIP
│   ├── llm.py               # Промпты, extract_json, plan_with_claude, agent_chat_reply
│   ├── executor.py          # apply_plan — детерминированные трансформации
│   ├── schemas.py           # Pydantic: AnalystReport, CleaningPlan, BinSpec, …
│   ├── domain_presets.py    # FilePreset, доменные блоки для промпта
│   ├── binning_metrics.py   # WoE, IV, KS, Gini, guess_target_column
│   ├── word_report.py       # report.docx (matplotlib + python-docx)
│   ├── prep_context.py      # Контекст для чата, X-Prep-Context
│   ├── chat_models.py       # ChatRequest / ChatResponse
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── app.css
├── requirements.txt
├── .env                     # ключи (не коммитить)
└── README.md
```

### Поток данных в коде

| Этап | Модуль |
|------|--------|
| Чтение CSV/XLSX | `main._load_frame` |
| Промпт планировщика | `llm.planner_prompt` + `domain_presets.preset_domain_block` |
| Вызов Claude | `llm.plan_with_claude` → `AnalystReport` |
| Применение плана | `executor.apply_plan` |
| Отчёт Word | `word_report.build_word_report_bytes` |
| Контекст чата | `prep_context.build_processing_context` |

---

## Ограничения и безопасность

- **Объём данных:** в LLM уходит превью **первых 50 строк** и агрегаты (типы, доли NA); полный файл обрабатывается локально в pandas. Очень широкие таблицы могут не поместиться в контекст модели — план строится по выборке.
- **Стоимость и лимиты API:** каждая обработка — минимум один вызов Claude; чат — отдельные вызовы. Следите за квотами Anthropic.
- **Секреты:** храните `ANTHROPIC_API_KEY` только в `.env`; не публикуйте ключ в репозитории и скриншотах.
- **Конфиденциальность:** файлы обрабатываются на машине, где запущен сервер; фрагменты данных передаются в Anthropic при планировании и в чате — учитывайте политику вашей организации.
- **Воспроизводимость:** при смене модели или температуры план может отличаться; исполнение при **одном и том же** JSON-плане детерминировано.
- **Нет обучения модели:** сервис только готовит таблицу; обучение sklearn/LightGBM и т.д. — вне scope.
- **Размер `processing_context`:** ограничение ~52 KB в запросе чата.

---

## Устранение неполадок

| Симптом | Что проверить |
|---------|----------------|
| `ANTHROPIC_API_KEY` не задан | Файл `.env` в корне, перезапуск uvicorn |
| `502` на `/prepare-dataset` | Доступ к API, валидность JSON от модели; откройте `/docs` и посмотрите `detail` |
| `503` на `/chat` | То же для ключа API |
| Ошибки numpy/pandas в venv | Удалите `.venv`, создайте заново: `python -m venv .venv`, `pip install -r requirements.txt` |
| Превью не обновляется в браузере | Жёсткое обновление Ctrl+F5; статика с `Cache-Control: no-store` только для `/` |
| `docx_report` вернул `report_docx_error.txt` | Нет таргета, ошибка matplotlib/docx, смотрите текст в файле внутри ZIP |
| Пустой или странный биннинг | Мало уникальных значений в колонке, все NA — смотрите `execution_log_ru` |
| Excel не читается | Установлен `openpyxl`; для `.xls` (старый формат) конвертируйте в `.xlsx` |

### Логи при разработке

Запуск с выводом в консоль:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --log-level debug
```

---

## Лицензия

Уточните лицензию для вашего репозитория при публикации (MIT, Apache-2.0, проприетарная и т.д.).
