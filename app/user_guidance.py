"""Парсинг и форматирование пользовательских инструкций для LLM."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.schemas import UserDatasetGuidance


def parse_user_guidance(raw: str | None) -> UserDatasetGuidance | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"user_guidance: невалидный JSON — {exc}") from exc
    if data is None:
        return None
    try:
        return UserDatasetGuidance.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"user_guidance: {exc}") from exc


def guidance_for_planner(guidance: UserDatasetGuidance | None) -> str:
    if guidance is None or guidance.model_dump(exclude_defaults=True) == {}:
        return ""
    parts: list[str] = [
        "\n--- ИНСТРУКЦИИ ПОЛЬЗОВАТЕЛЯ (обязательны к учёту в плане) ---",
    ]

    desc_text = (guidance.column_descriptions_text or "").strip()
    if desc_text:
        parts.append("\nОПИСАНИЕ КОЛОНОК (текст пользователя, смысл полей):")
        parts.append(desc_text)
    elif guidance.column_descriptions:
        parts.append("\nОПИСАНИЕ КОЛОНОК (смысл полей):")
        for d in guidance.column_descriptions[:40]:
            parts.append(f"- «{d.column}»: {d.description.strip()}")

    if guidance.validation_rules:
        parts.append(
            "\nПРАВИЛА ВАЛИДАЦИИ (добавь в custom_validation те же rule/params; "
            "дублируй в validation_notes кратко):"
        )
        for r in guidance.validation_rules[:35]:
            params = json.dumps(r.params, ensure_ascii=False) if r.params else "{}"
            parts.append(
                f"- колонка «{r.column}», rule={r.rule}, params={params}, "
                f"on_fail={r.on_fail}"
                + (f", note={r.note}" if r.note else "")
            )

    if guidance.freeform_instructions:
        parts.append("\nДОПОЛНИТЕЛЬНЫЕ ТРЕБОВАНИЯ (текст):")
        for line in guidance.freeform_instructions[:20]:
            t = line.strip()
            if t:
                parts.append(f"- {t}")

    parts.append(
        "\nВ JSON-плане отрази custom_validation[] с теми же rule, что выше. "
        "В summary_ru и validation_notes укажи, какие проверки применены."
    )
    parts.append("--- конец инструкций пользователя ---\n")
    return "\n".join(parts)


def guidance_summary_dict(guidance: UserDatasetGuidance | None) -> dict[str, Any] | None:
    if guidance is None:
        return None
    return {
        "column_descriptions_text": (guidance.column_descriptions_text or "")[:800],
        "validation_rules_count": len(guidance.validation_rules),
        "validation_rules_sample": [
            {"column": r.column, "rule": r.rule, "on_fail": r.on_fail} for r in guidance.validation_rules[:15]
        ],
        "freeform_instructions": [s[:400] for s in guidance.freeform_instructions[:10]],
    }
