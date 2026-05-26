"""Claude planner: описание набора трансформаций в строгом JSON."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

import anthropic

from app.domain_presets import FilePreset, preset_domain_block
from app.schemas import AnalystReport, UserDatasetGuidance
from app.user_guidance import guidance_for_planner

_TRANSITION_MATRIX_JSON = """
  "econometric_notes": [],
  "transition_matrix": {
    "product_label": "Денежный 1",
    "bucket_column": "basket_tr",
    "period_column": "period",
    "macro_columns": ["real_gdp", "GDD_Min_R", "GDD_Elc_R", "GDD_Con_R", "GDD_Trd_R", "GDD_Trn_R"],
    "transition_rate_columns": ["1>1", "1>2", "1>3", "2>1", "2>2", "2>3", "3>1", "3>2", "3>3"],
    "dependent_column": "2>1",
    "transform_macro": true,
    "transform_targets": true,
    "feature_transforms": [
      {"column": "real_gdp", "transforms": ["qoq_abs", "qoq_rel", "log", "zscore"]},
      {"column": "1>1", "transforms": ["qoq_abs", "qoq_rel", "log", "zscore"]}
    ]
  },
"""


def _planner_json_schema(preset: FilePreset) -> str:
    base = """
{
  "summary_ru": "краткое резюме",
  "cleaning_notes": [],
  "validation_notes": [],
  "binning_notes": [],"""
    if preset == FilePreset.credit_transition_matrix:
        base += _TRANSITION_MATRIX_JSON
    base += """
  "cleaning": {
    "dedupe": "none|full|subset",
    "dedupe_columns": [],
    "drop_columns": [],
    "drop_rows_all_na": true/false,
    "fill_na": {"column": {"strategy": "median|mean|mode|constant|drop_rows", "constant": null}} },
    "numeric_outliers": null или {"columns": [], "mode": "winsorize_iqr|drop_iqr", "iqr_multiplier": 1.5},
    "strip_whitespace": null или {"columns": null}
  },
  "validation": {
    "cast_columns": [{"column": "", "dtype": "int64|float64|string|category|datetime64[ns]|boolean"}],
    "numeric_clip": []
  },
  "custom_validation": [
    {"column": "birth_date", "rule": "date_year_min", "params": {"min_year": 1920}, "on_fail": "null", "note": ""}
  ],
  "binning": [
    {"column": "", "output_column": "", "method": "quantile|equal", "n_bins": 5, "labels": null}
  ]
}"""
    return base


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = JSON_BLOCK_RE.search(text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def planner_prompt(
    filename: str,
    columns_info: str,
    preview_csv: str,
    null_rates: dict[str, float],
    n_rows: int,
    preset: FilePreset = FilePreset.individual_scoring,
    user_guidance: UserDatasetGuidance | None = None,
) -> str:
    nr = json.dumps(null_rates, ensure_ascii=False)
    domain = preset_domain_block(preset)
    preset_label = preset.value
    user_block = guidance_for_planner(user_guidance)
    return f"""Ты — ведущий ML-инженер по подготовке табличных данных. Файл: {filename}
Строк (после чтения): {n_rows}
ВЫБРАННЫЙ ПРЕСЕТ МОДЕЛИ (пользователь): {preset_label}

--- доменные правила пресета ---
{domain}
--- конец доменных правил ---
{user_block}
ДОЛЯ ПРОПУСКОВ ПО КОЛОНКАМ (0..1):
{nr}

КОЛОНКИ И ТИПЫ (pandas):
{columns_info}

ПРЕДПРОСМОТР (первые 50 строк CSV):
{preview_csv}

Спроектируй план трансформаций для обучения модели с учётом пресета выше.
Верни ТОЛЬКО один JSON без поясняющего текста до/после. Соблюдай схему:

{_planner_json_schema(preset)}

Правила:
- dedupe: "full" если есть явные строковые ключи строки-дубликата и это уместно; "subset" + dedupe_columns для бизнес-ключа; иначе "none".
- fill_na: для целочисленных предикторов можно median/mean; для категорий — mode или constant "UNKNOWN".
- numeric_outliers: только для действительно шумных метрик (цена/суммы), не удаляй большинство целевой выборки.
- cast_columns: приведите к осмысленным типам; даты как datetime64[ns].
- binning: добавь только для нескольких важных непрерывных признаков; output_column суффикс _binned; labels — опционально: если задан массив той же длины, подставляется как есть; если null, в CSV пишутся номера групп 1…K по порядку интервалов (а не строки вида «(653.5, 682.0]»).
- Не включай колонку-цель в список на удаление, если она есть — определи по контексту имя возможной колонки target и упомянь в validation_notes если неясно.
- custom_validation: правила с полями column, rule (must_be_numeric|must_be_integer|must_be_datetime|date_year_min|date_year_max|numeric_min|numeric_max|string_length_min|string_length_max|regex_extract_digits|regex_extract|regex_replace|regex_must_match|not_null|allowed_values|unique), params, on_fail (null|drop_row|keep). Исполнитель применит их детерминированно.
- Если пользователь дал ОПИСАНИЕ КОЛОНОК — используй смысл полей при выборе cast_columns, fill_na и validation_notes.
- Для пресета credit_transition_matrix: обязательно заполни transition_matrix (корзина, период, макрофакторы, feature_transforms с qoq_abs/qoq_rel/log/zscore) и econometric_notes.

JSON должен быть валидным UTF-8."""


AGENT_CHAT_SYSTEM = """Ты опытный ML-инженер и аналитик данных. Помогаешь с подготовкой табличных датасетов:
очистка пропусков и дубликатов, валидация типов и диапазонов, кодирование категорий, биннинг чисел,
выбор признаков, утечки данных, train/test split, метрики.

Отвечай по-русски, структурированно (списки и короткие абзацы).

Если с сообщением передан блок «КОНТЕКСТ ПОСЛЕДНЕЙ ОБРАБОТКИ ФАЙЛА» (JSON):
- Считай, что вопросы пользователя часто про ЭТОТ конкретный запуск (что сделали, почему так, какие колонки затронуты).
- Связывай ответ с summary_ru, заметками (cleaning_notes, validation_notes, econometric_notes), планом cleaning_plan / validation_plan / custom_validation, binning_specs, user_guidance_summary, transition_matrix_plan, transition_econometrics_summary и журналом execution_log_ru.
- Если preview_plan_only=true — ясно скажи: это только план в JSON, строки датафрейма ещё не менялись исполнителем.
- Не выдумывай столбцы вне columns_sample и не утверждай факты, которых нет в контексте.

Если контекста обработки нет — не придумывай содержимое файлов; предложи загрузить файл и нажать «Обработать»."""

CONTEXT_BLOCK_TITLE = "\n\n--- КОНТЕКСТ ПОСЛЕДНЕЙ ОБРАБОТКИ ФАЙЛА (JSON, один запуск на странице) ---\n"


def agent_chat_reply(
    history: list[dict[str, str]],
    user_message: str,
    *,
    processing_context: dict[str, Any] | None = None,
    max_tokens: int = 4096,
) -> str:
    """history: роли user|assistant, последние сообщения первыми не требуются — передаётся полная история."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан ANTHROPIC_API_KEY")

    system = AGENT_CHAT_SYSTEM
    if processing_context:
        ctx_json = json.dumps(processing_context, ensure_ascii=False, indent=2)
        if len(ctx_json) > 18000:
            ctx_json = ctx_json[:18000] + "\n… [обрезано]"
        system = AGENT_CHAT_SYSTEM + CONTEXT_BLOCK_TITLE + ctx_json

    client = anthropic.Anthropic(api_key=api_key)
    msgs: list[dict[str, str]] = []
    for turn in history[-30:]:
        r = turn.get("role")
        c = (turn.get("content") or "").strip()
        if r not in ("user", "assistant") or not c:
            continue
        msgs.append({"role": r, "content": c[:32000]})
    msgs.append({"role": "user", "content": user_message.strip()[:32000]})

    msg = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=max_tokens,
        temperature=0.4,
        system=system,
        messages=msgs,
    )
    parts: list[str] = []
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def plan_with_claude(prompt: str) -> AnalystReport:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: list[str] = []
    for block in msg.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    raw = "".join(parts)
    data = extract_json(raw)
    return AnalystReport.model_validate(data)
