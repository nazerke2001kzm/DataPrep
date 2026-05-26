"""Apply AnalystReport transforms to a pandas DataFrame."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.custom_validation import apply_validation_rules, merge_validation_rules
from app.schemas import AnalystReport, UserDatasetGuidance
from app.transition_analysis import apply_transition_features


def _iqr_bounds(s: pd.Series, k: float) -> tuple[float, float]:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return float("nan"), float("nan")
    q1 = s.quantile(0.25)
    q3 = s.quantile(0.75)
    iqr = q3 - q1
    return float(q1 - k * iqr), float(q3 + k * iqr)


def apply_plan(
    df: pd.DataFrame,
    plan: AnalystReport,
    *,
    user_guidance: UserDatasetGuidance | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    log: list[str] = []
    c = plan.cleaning

    if c.dedupe == "full":
        before = len(out)
        out = out.drop_duplicates().reset_index(drop=True)
        log.append(f"Удалены полные дубликаты: {before - len(out)} строк")
    elif c.dedupe == "subset":
        cols = [x for x in c.dedupe_columns if x in out.columns]
        if cols:
            before = len(out)
            out = out.drop_duplicates(subset=cols).reset_index(drop=True)
            log.append(f"Дубликаты по {cols}: −{before - len(out)} строк")

    for col in c.drop_columns:
        if col in out.columns:
            out = out.drop(columns=[col])
            log.append(f"Удалена колонка «{col}»")

    if c.drop_rows_all_na:
        before = len(out)
        out = out.dropna(how="all").reset_index(drop=True)
        log.append(f"Пустые строки удалены: {before - len(out)}")

    sw = c.strip_whitespace
    if sw is not None:
        obj_cols = (
            out.select_dtypes(include=["object", "string"]).columns.tolist()
            if sw.columns is None
            else [x for x in sw.columns if x in out.columns]
        )
        for col in obj_cols:
            out[col] = out[col].map(lambda x: x.strip() if isinstance(x, str) else x)
        if obj_cols:
            log.append("trim строк: " + ", ".join(obj_cols))

    no = c.numeric_outliers
    if no is not None:
        active = [
            name
            for name in no.columns
            if name in out.columns and pd.to_numeric(out[name], errors="coerce").notna().any()
        ]
        for col in active:
            ser = pd.to_numeric(out[col], errors="coerce")
            lo, hi = _iqr_bounds(ser, no.iqr_multiplier)
            if pd.isna(lo) or pd.isna(hi):
                continue
            if no.mode == "drop_iqr":
                mask = ser.between(lo, hi, inclusive="both") | ser.isna()
                removed = int((~mask).sum())
                out = out.loc[mask].reset_index(drop=True)
                log.append(f"«{col}»: выбросы IQR (×{no.iqr_multiplier}), удалено {removed}")
            else:
                out[col] = ser.clip(lower=lo, upper=hi)
                log.append(f"«{col}»: winsorize IQR [{lo:g}; {hi:g}]")

    for col, spec in c.fill_na.items():
        if col not in out.columns:
            continue
        if spec.strategy == "drop_rows":
            before = len(out)
            out = out.dropna(subset=[col]).reset_index(drop=True)
            log.append(f"«{col}»: строк с NA удалено {before - len(out)} → осталось {len(out)}")
            continue
        ser = out[col]
        if spec.strategy == "median":
            fill = pd.to_numeric(ser, errors="coerce").median()
        elif spec.strategy == "mean":
            fill = pd.to_numeric(ser, errors="coerce").mean()
        elif spec.strategy == "mode":
            m = ser.mode()
            fill = m.iloc[0] if len(m) else np.nan
        elif spec.strategy == "constant":
            fill = spec.constant
        else:
            fill = np.nan
        out[col] = ser.fillna(fill)
        log.append(f"«{col}»: заполнены пропуски ({spec.strategy})")

    v = plan.validation
    for cast in v.cast_columns:
        if cast.column not in out.columns:
            continue
        col = cast.column
        try:
            if cast.dtype == "datetime64[ns]":
                out[col] = pd.to_datetime(out[col], errors="coerce")
            elif cast.dtype == "category":
                out[col] = out[col].astype("category")
            elif cast.dtype == "boolean":
                out[col] = out[col].astype("boolean")
            elif cast.dtype == "int64":
                out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
            elif cast.dtype == "float64":
                out[col] = pd.to_numeric(out[col], errors="coerce")
            elif cast.dtype == "string":
                out[col] = out[col].astype("string")
            log.append(f"«{col}» → {cast.dtype}")
        except Exception as exc:  # noqa: BLE001
            log.append(f"«{col}»: ошибка типа {cast.dtype}: {exc}")

    for clip in v.numeric_clip:
        if clip.column not in out.columns:
            continue
        ser = pd.to_numeric(out[clip.column], errors="coerce")
        out[clip.column] = ser.clip(lower=clip.min, upper=clip.max)
        log.append(f"«{clip.column}»: ограничение [{clip.min}, {clip.max}]")

    val_rules = merge_validation_rules(
        user_rules=user_guidance.validation_rules if user_guidance else None,
        plan_rules=plan.custom_validation,
    )
    if val_rules:
        out, vlog = apply_validation_rules(out, val_rules)
        log.extend(vlog)

    if plan.transition_matrix is not None:
        out, tlog = apply_transition_features(out, plan.transition_matrix)
        log.extend(tlog)

    for b in plan.binning:
        if b.column not in out.columns:
            continue
        s = pd.to_numeric(out[b.column], errors="coerce")
        try:
            if b.method == "quantile":
                binned = pd.qcut(s, q=b.n_bins, duplicates="drop")
            else:
                binned = pd.cut(s, bins=b.n_bins, duplicates="drop")
            if getattr(binned, "cat", None) is None:
                out[b.output_column] = pd.Series(pd.NA, index=out.index, dtype="string")
                log.append(f"Биннинг «{b.column}» пропуск: результат не categorical")
                continue
            bins = len(binned.cat.categories)
            if b.labels is not None and bins and len(b.labels) == bins:
                labeled = binned.rename_categories(list(b.labels))
                out[b.output_column] = labeled.astype("string").where(~s.isna(), pd.NA)
                log.append(
                    f"Биннинг «{b.column}» → «{b.output_column}» ({b.method}, {b.n_bins}; метки из labels)"
                )
            else:
                # Группы 1…K по порядку интервалов (слева направо), без сырых границ (653, 682] в CSV.
                codes = binned.cat.codes
                out[b.output_column] = (codes + 1).where(codes >= 0, pd.NA).astype("Int64")
                log.append(
                    f"Биннинг «{b.column}» → «{b.output_column}» ({b.method}, {b.n_bins}; группы 1…{bins})"
                )
        except Exception as exc:  # noqa: BLE001
            log.append(f"Биннинг «{b.column}» пропуск: {exc}")

    return out, log
