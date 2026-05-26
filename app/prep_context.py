"""Компактный контекст последней обработки файла — для чата с моделью."""

from __future__ import annotations

import base64
import json
from typing import Any

from app.schemas import UserDatasetGuidance
from app.user_guidance import guidance_summary_dict


def build_processing_context(
    *,
    filename: str,
    preset_value: str,
    preset_label_ru: str,
    rows_before: int,
    rows_after: int | None,
    column_names: list[str],
    report: dict[str, Any],
    preview_plan_only: bool,
    user_guidance: UserDatasetGuidance | None = None,
) -> dict[str, Any]:
    """Без сырых строк таблицы — план, заметки и лог исполнения."""

    def clip_list(val: Any, n: int) -> list[Any]:
        if not isinstance(val, list):
            return []
        return val[:n]

    cleaning = report.get("cleaning") if isinstance(report.get("cleaning"), dict) else {}
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    binning = report.get("binning") if isinstance(report.get("binning"), list) else []

    return {
        "preset": preset_value,
        "preset_label_ru": preset_label_ru,
        "filename": filename,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "preview_plan_only": preview_plan_only,
        "columns_sample": column_names[:60],
        "summary_ru": (report.get("summary_ru") or "")[:1200],
        "cleaning_notes": clip_list(report.get("cleaning_notes"), 18),
        "validation_notes": clip_list(report.get("validation_notes"), 18),
        "binning_notes": clip_list(report.get("binning_notes"), 12),
        "econometric_notes": clip_list(report.get("econometric_notes"), 14),
        "execution_log_ru": clip_list(report.get("execution_log_ru"), 24),
        "cleaning_plan": {
            k: cleaning.get(k)
            for k in (
                "dedupe",
                "dedupe_columns",
                "drop_columns",
                "drop_rows_all_na",
                "fill_na",
                "numeric_outliers",
                "strip_whitespace",
            )
            if k in cleaning
        },
        "validation_plan": {
            "cast_columns": clip_list(validation.get("cast_columns"), 30),
            "numeric_clip": clip_list(validation.get("numeric_clip"), 15),
        },
        "binning_specs": clip_list(binning, 15),
        "transition_matrix_plan": _clip_transition_plan(report.get("transition_matrix")),
        "transition_econometrics_summary": _clip_econometrics(report.get("transition_econometrics")),
        "user_guidance_summary": guidance_summary_dict(user_guidance)
        or report.get("user_guidance_applied"),
        "custom_validation_plan": clip_list(report.get("custom_validation"), 20),
    }


def _clip_transition_plan(val: Any) -> dict[str, Any] | None:
    if not isinstance(val, dict):
        return None
    return {
        k: val.get(k)
        for k in (
            "product_label",
            "bucket_column",
            "period_column",
            "macro_columns",
            "transition_rate_columns",
            "dependent_column",
        )
        if k in val
    }


def _clip_econometrics(val: Any) -> dict[str, Any] | None:
    if not isinstance(val, dict):
        return None
    rec = val.get("recommendations_ru")
    cols = val.get("columns_analyzed")
    return {
        "recommendations_ru": rec[:8] if isinstance(rec, list) else [],
        "recommended_lags_global": (val.get("recommended_lags_global") or [])[:8],
        "vif_summary_ru": (val.get("vif") or {}).get("summary_ru"),
        "adjusted_r2_summary_ru": (val.get("adjusted_r2_ranking") or {}).get("summary_ru"),
        "adjusted_r2_targets": list((val.get("adjusted_r2_by_target") or {}).keys())[:9],
        "columns_analyzed": cols[:20] if isinstance(cols, list) else [],
    }


def context_header_value(ctx: dict[str, Any]) -> str:
    raw = json.dumps(ctx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.standard_b64encode(raw).decode("ascii")
