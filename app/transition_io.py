"""Загрузка и склейка файлов «макрофакторы + корзина TR» (кварталы 2010Q1, GDD_*, 1>1…3>3)."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

_QUARTER_RE = re.compile(r"^(\d{4})\s*Q\s*([1-4])$", re.IGNORECASE)
_TRANSITION_COL_RE = re.compile(r"^\s*([123])\s*>\s*([123])\s*$")
_MACRO_NAME_RE = re.compile(r"^(real_gdp|GDD_[A-Za-z0-9_]+)$", re.IGNORECASE)

_PERIOD_HEADERS = (
    "period",
    "quarter",
    "квартал",
    "дата",
    "date",
    "переход",
    "дата/переход",
)


def normalize_quarter_label(value: Any) -> str | None:
    """2010Q1, 2010 Q1, datetime → единый ключ YYYYQX."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        q = (value.month - 1) // 3 + 1
        return f"{value.year}Q{q}"
    s = str(value).strip().upper().replace(" ", "")
    m = _QUARTER_RE.match(s)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    if hasattr(value, "year") and hasattr(value, "month"):
        q = (int(value.month) - 1) // 3 + 1
        return f"{int(value.year)}Q{q}"
    return None


def coerce_ru_numeric(series: pd.Series) -> pd.Series:
    """Пробел — разряды, запятая — десятичный разделитель (95,1 → 95.1)."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    def _one(x: Any) -> float | None:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return float(x)
        s = str(x).strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
        if not s or s in (".", "-"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    return series.map(_one)


def _row_looks_like_quarter(cell: Any) -> bool:
    return normalize_quarter_label(cell) is not None


def _sheet_kind(df: pd.DataFrame) -> str:
    """macro | transition | unknown"""
    flat = " ".join(str(c) for c in df.astype(str).values.ravel()[:500])
    if _TRANSITION_COL_RE.search(flat.replace(" ", "")) or re.search(r"[123]\s*>\s*[123]", flat):
        return "transition"
    if _MACRO_NAME_RE.search(flat) or "real_gdp" in flat.lower() or "GDD_" in flat:
        return "macro"
    return "unknown"


def _extract_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Находит строку заголовка и данные с кварталами в первом столбце."""
    if raw.empty:
        return raw

    header_idx: int | None = None
    data_start: int | None = None

    for i in range(min(25, len(raw))):
        row = raw.iloc[i]
        texts = [str(x).strip() for x in row if pd.notna(x) and str(x).strip()]
        if any(_MACRO_NAME_RE.match(t) for t in texts):
            header_idx = i
        if any(_TRANSITION_COL_RE.match(t.replace(" ", "")) for t in texts):
            header_idx = i if header_idx is None else header_idx
        if _row_looks_like_quarter(row.iloc[0]):
            data_start = i
            break

    if header_idx is None and data_start is not None:
        header_idx = max(0, data_start - 1)

    if header_idx is None:
        out = raw.copy()
        out.columns = [str(c) for c in out.columns]
        return out

    headers = [str(x).strip() if pd.notna(x) else f"col_{j}" for j, x in enumerate(raw.iloc[header_idx])]
    start = data_start if data_start is not None else header_idx + 1
    body = raw.iloc[start:].copy()
    body.columns = headers[: len(body.columns)]
    body = body.dropna(how="all")
    if len(body) == 0:
        return body

    period_col = body.columns[0]
    body = body[_row_looks_like_quarter(body.iloc[:, 0])].copy()
    body["_period_key"] = body[period_col].map(normalize_quarter_label)
    body = body.dropna(subset=["_period_key"])

    for col in body.columns:
        if col in (period_col, "_period_key"):
            continue
        body[col] = coerce_ru_numeric(body[col])

    body = body.rename(columns={period_col: "period"})
    return body.reset_index(drop=True)


def _rename_transition_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mapping: dict[str, str] = {}
    for c in out.columns:
        m = _TRANSITION_COL_RE.match(str(c).replace(" ", ""))
        if m:
            mapping[c] = f"{m.group(1)}>{m.group(2)}"
    if mapping:
        out = out.rename(columns=mapping)
    return out


def guess_transition_rate_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for c in df.columns:
        if _TRANSITION_COL_RE.match(str(c).replace(" ", "")):
            cols.append(str(c))
    order = {f"{i}>{j}": i * 3 + j for i in (1, 2, 3) for j in (1, 2, 3)}
    cols.sort(key=lambda x: order.get(str(x).replace(" ", ""), 99))
    return cols


def guess_kz_macro_columns(df: pd.DataFrame) -> list[str]:
    preferred = [
        "real_gdp",
        "GDD_Min_R",
        "GDD_Elc_R",
        "GDD_Con_R",
        "GDD_Trd_R",
        "GDD_Trn_R",
    ]
    found = [c for c in preferred if c in df.columns]
    if found:
        return found
    out: list[str] = []
    for c in df.columns:
        if c in ("period", "_period_key", "product_tr", "basket_tr"):
            continue
        k = str(c)
        if _MACRO_NAME_RE.match(k) or k.lower().startswith("gdd_") or "gdp" in k.lower():
            out.append(k)
    return out


def merge_macro_and_transition(macro: pd.DataFrame, transition: pd.DataFrame) -> pd.DataFrame:
    m = macro.copy()
    t = _rename_transition_columns(transition.copy())
    if "_period_key" not in m.columns and "period" in m.columns:
        m["_period_key"] = m["period"].map(normalize_quarter_label)
    if "_period_key" not in t.columns and "period" in t.columns:
        t["_period_key"] = t["period"].map(normalize_quarter_label)

    tr_cols = [c for c in t.columns if c not in ("period", "_period_key") and c in guess_transition_rate_columns(t)]
    m_cols = [c for c in m.columns if c not in ("period", "_period_key")]

    m_use = m[["_period_key"] + (["period"] if "period" in m.columns else []) + m_cols]
    merged = pd.merge(
        m_use,
        t[["_period_key"] + tr_cols],
        on="_period_key",
        how="inner",
    )
    merged = merged.sort_values("_period_key").reset_index(drop=True)
    merged["product_tr"] = merged.get("product_tr", "TR")
    merged["basket_tr"] = merged.get("basket_tr", "Денежный 1")
    return merged


def validate_transition_row_sums(df: pd.DataFrame, tr_cols: list[str]) -> list[str]:
    """Проверка: для каждого исходного состояния i сумма i>j ≈ 100 (%)."""
    notes: list[str] = []
    by_from: dict[str, list[str]] = {"1": [], "2": [], "3": []}
    col_map = {str(c).replace(" ", ""): str(c) for c in df.columns}
    for c in tr_cols:
        key = str(c).replace(" ", "")
        m = _TRANSITION_COL_RE.match(key)
        if m:
            by_from[m.group(1)].append(col_map.get(key, str(c)))

    for state, group in by_from.items():
        if len(group) < 2:
            continue
        present = [c for c in group if c in df.columns]
        if not present:
            continue
        row_sum = df[present].sum(axis=1)
        mean_sum = float(row_sum.mean())
        if abs(mean_sum - 100) > 5 and abs(mean_sum - 1.0) > 0.05:
            notes.append(
                f"Состояние {state}: средняя сумма переходов {mean_sum:.1f} "
                f"(ожидалось ~100% или ~1.0); проверьте масштаб"
            )
    return notes


def load_transition_workbook(filename: str, raw: bytes) -> pd.DataFrame:
    """Excel: один лист (уже merged) или два — макро + TR."""
    buf = io.BytesIO(raw)
    lower = filename.lower()
    if lower.endswith(".csv"):
        for kwargs in (
            {"sep": None, "engine": "python"},
            {"sep": ";", "decimal": ",", "thousands": " "},
            {"sep": ",", "decimal": "."},
        ):
            try:
                buf.seek(0)
                raw_df = pd.read_csv(buf, **kwargs)
                if len(raw_df.columns) > 1:
                    return prepare_single_frame(_extract_table(raw_df) if _sheet_kind(raw_df) == "unknown" else raw_df)
            except Exception:  # noqa: BLE001
                continue
        buf.seek(0)
        return prepare_single_frame(pd.read_csv(buf))

    if not lower.endswith((".xlsx", ".xlsm")):
        raise ValueError("ожидается .csv, .xlsx или .xlsm")

    xl = pd.ExcelFile(buf)
    parsed: dict[str, pd.DataFrame] = {}
    for name in xl.sheet_names:
        raw_sheet = pd.read_excel(xl, sheet_name=name, header=None)
        parsed[name] = _extract_table(raw_sheet)

    kinds = {n: _sheet_kind(df) for n, df in parsed.items()}
    macro_sheets = [n for n, k in kinds.items() if k == "macro"]
    tr_sheets = [n for n, k in kinds.items() if k == "transition"]

    if macro_sheets and tr_sheets:
        return merge_macro_and_transition(parsed[macro_sheets[0]], parsed[tr_sheets[0]])

    if len(parsed) == 1:
        return prepare_single_frame(next(iter(parsed.values())))

    # несколько листов без явной метки — эвристика по колонкам
    for df in parsed.values():
        if guess_transition_rate_columns(df) and guess_kz_macro_columns(df):
            return prepare_single_frame(df)
    frames = list(parsed.values())
    if len(frames) >= 2:
        return merge_macro_and_transition(frames[0], frames[1])
    return prepare_single_frame(frames[0])


def prepare_single_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = _rename_transition_columns(df.copy())
    if "period" not in out.columns and len(out.columns):
        first = out.columns[0]
        if out[first].map(normalize_quarter_label).notna().any():
            out = out.rename(columns={first: "period"})
    if "period" in out.columns:
        out["_period_key"] = out["period"].map(normalize_quarter_label)
        out = out.sort_values("_period_key").reset_index(drop=True)
    for col in out.columns:
        if col not in ("period", "_period_key", "product_tr", "basket_tr"):
            if not pd.api.types.is_numeric_dtype(out[col]):
                out[col] = coerce_ru_numeric(out[col])
    if "product_tr" not in out.columns:
        out["product_tr"] = "TR"
    if "basket_tr" not in out.columns:
        out["basket_tr"] = "Денежный 1"
    return out
