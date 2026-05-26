"""Детерминированное применение пользовательских правил валидации."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from app.schemas import ValidationRule

_RULE_ALIASES: dict[str, str] = {
    "numeric": "must_be_numeric",
    "integer": "must_be_integer",
    "datetime": "must_be_datetime",
    "date_min_year": "date_year_min",
    "birth_year_min": "date_year_min",
    "max_length": "string_length_max",
    "min_length": "string_length_min",
    "regex_digits": "regex_extract_digits",
    "extract_digits": "regex_extract_digits",
    "only_digits": "regex_extract_digits",
}


def _norm_rule(rule: ValidationRule) -> ValidationRule:
    r = _RULE_ALIASES.get(rule.rule, rule.rule)
    if r == rule.rule:
        return rule
    return rule.model_copy(update={"rule": r})


def merge_validation_rules(
    *,
    user_rules: list[ValidationRule] | None,
    plan_rules: list[ValidationRule] | None,
) -> list[ValidationRule]:
    """Пользовательские правила первыми; дубликаты column+rule — пользователь важнее."""
    seen: set[tuple[str, str]] = set()
    out: list[ValidationRule] = []
    for raw in (user_rules or []) + (plan_rules or []):
        r = _norm_rule(raw)
        key = (r.column, r.rule)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _apply_fail_mask(
    out: pd.DataFrame,
    mask_fail: pd.Series,
    on_fail: str,
    log: list[str],
    msg: str,
) -> pd.DataFrame:
    n = int(mask_fail.sum())
    if n == 0:
        return out
    if on_fail == "drop_row":
        out = out.loc[~mask_fail].reset_index(drop=True)
        log.append(f"{msg}: удалено строк {n}")
    else:
        log.append(f"{msg}: нарушений {n} (значения обнулены/NA)")
    return out


def apply_validation_rules(
    df: pd.DataFrame,
    rules: list[ValidationRule],
) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    log: list[str] = []

    for raw in rules:
        rule = _norm_rule(raw)
        col = rule.column
        if col not in out.columns:
            log.append(f"Правило «{rule.rule}» для «{col}»: колонка не найдена")
            continue

        p = rule.params or {}
        on_fail = rule.on_fail
        note = f" ({rule.note})" if rule.note else ""
        ser = out[col]

        try:
            if rule.rule == "must_be_numeric":
                coerced = pd.to_numeric(ser, errors="coerce")
                mask_fail = ser.notna() & coerced.isna()
                out[col] = coerced
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: не число{note}",
                )
                if on_fail == "null" and mask_fail.any():
                    out.loc[mask_fail, col] = np.nan

            elif rule.rule == "must_be_integer":
                num = pd.to_numeric(ser, errors="coerce")
                intish = num.round() == num
                mask_fail = ser.notna() & (num.isna() | ~intish)
                out[col] = num.round().astype("Int64")
                out = _apply_fail_mask(out, mask_fail, on_fail, log, f"«{col}»: не целое{note}")

            elif rule.rule == "must_be_datetime":
                dt = pd.to_datetime(ser, errors="coerce")
                mask_fail = ser.notna() & dt.isna()
                out[col] = dt
                out = _apply_fail_mask(out, mask_fail, on_fail, log, f"«{col}»: не дата{note}")

            elif rule.rule == "date_year_min":
                min_y = int(p.get("min_year", p.get("year", 1920)))
                dt = pd.to_datetime(ser, errors="coerce")
                out[col] = dt
                mask_fail = dt.notna() & (dt.dt.year < min_y)
                if on_fail == "null":
                    out.loc[mask_fail, col] = pd.NaT
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: год < {min_y}{note}",
                )

            elif rule.rule == "date_year_max":
                max_y = int(p.get("max_year", p.get("year", 2100)))
                dt = pd.to_datetime(ser, errors="coerce")
                out[col] = dt
                mask_fail = dt.notna() & (dt.dt.year > max_y)
                if on_fail == "null":
                    out.loc[mask_fail, col] = pd.NaT
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: год > {max_y}{note}",
                )

            elif rule.rule == "numeric_min":
                lo = float(p.get("min", p.get("value", 0)))
                num = pd.to_numeric(ser, errors="coerce")
                mask_fail = num.notna() & (num < lo)
                out[col] = num.clip(lower=lo)
                out = _apply_fail_mask(out, mask_fail, on_fail, log, f"«{col}»: < {lo}{note}")

            elif rule.rule == "numeric_max":
                hi = float(p.get("max", p.get("value", 0)))
                num = pd.to_numeric(ser, errors="coerce")
                mask_fail = num.notna() & (num > hi)
                out[col] = num.clip(upper=hi)
                out = _apply_fail_mask(out, mask_fail, on_fail, log, f"«{col}»: > {hi}{note}")

            elif rule.rule == "string_length_max":
                mx = int(p.get("max_length", p.get("length", 255)))
                lens = ser.astype("string").str.len()
                mask_fail = lens.notna() & (lens > mx)
                if on_fail == "null":
                    out.loc[mask_fail, col] = pd.NA
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: длина > {mx}{note}",
                )

            elif rule.rule == "string_length_min":
                mn = int(p.get("min_length", p.get("length", 1)))
                lens = ser.astype("string").str.len()
                mask_fail = ser.notna() & (lens < mn)
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: длина < {mn}{note}",
                )

            elif rule.rule == "regex_extract_digits":
                out_col = str(p.get("output_column") or col)
                as_int = bool(p.get("as_integer", False))
                extracted = ser.astype("string").str.replace(r"\D", "", regex=True)
                if as_int:
                    out[out_col] = pd.to_numeric(extracted, errors="coerce").astype("Int64")
                else:
                    out[out_col] = extracted.replace("", pd.NA)
                log.append(f"«{col}» → «{out_col}»: только цифры (regex){note}")

            elif rule.rule == "regex_extract":
                pattern = str(p.get("pattern", r"(\d+)"))
                out_col = str(p.get("output_column") or f"{col}_extracted")
                extracted = ser.astype("string").str.extract(pattern, expand=False)
                if isinstance(extracted, pd.DataFrame):
                    extracted = extracted.iloc[:, 0]
                out[out_col] = extracted
                log.append(f"«{col}» → «{out_col}»: extract /{pattern}/{note}")

            elif rule.rule == "regex_replace":
                pattern = str(p.get("pattern", ""))
                repl = str(p.get("replacement", p.get("repl", "")))
                out_col = str(p.get("output_column") or col)
                out[out_col] = ser.astype("string").str.replace(pattern, repl, regex=True)
                log.append(f"«{col}» → «{out_col}»: replace /{pattern}/{note}")

            elif rule.rule == "regex_must_match":
                pattern = str(p.get("pattern", r"^[\d]+$"))
                matched = ser.astype("string").str.match(pattern, na=False)
                mask_fail = ser.notna() & ~matched
                if on_fail == "null":
                    out.loc[mask_fail, col] = pd.NA
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: не совпало с /{pattern}/{note}",
                )

            elif rule.rule == "not_null":
                mask_fail = ser.isna() | (ser.astype("string").str.strip() == "")
                out = _apply_fail_mask(out, mask_fail, on_fail, log, f"«{col}»: пусто{note}")

            elif rule.rule == "allowed_values":
                allowed = p.get("values", p.get("allowed", []))
                if not isinstance(allowed, list):
                    allowed = []
                allowed_set = {str(v) for v in allowed}
                mask_fail = ser.notna() & ~ser.astype("string").isin(allowed_set)
                if on_fail == "null":
                    out.loc[mask_fail, col] = pd.NA
                out = _apply_fail_mask(
                    out,
                    mask_fail,
                    on_fail,
                    log,
                    f"«{col}»: значение вне списка ({len(allowed_set)} доп.){note}",
                )

            elif rule.rule == "unique":
                dup = out.duplicated(subset=[col], keep=False) & ser.notna()
                if on_fail == "drop_row":
                    before = len(out)
                    out = out.loc[~dup].reset_index(drop=True)
                    log.append(f"«{col}»: дубликаты −{before - len(out)} строк{note}")
                else:
                    log.append(f"«{col}»: найдено дубликатов {int(dup.sum())}{note}")

            else:
                log.append(f"«{col}»: неизвестное правило «{rule.rule}» — пропуск")

        except Exception as exc:  # noqa: BLE001
            log.append(f"«{col}» правило {rule.rule}: ошибка — {exc}")

    return out, log
