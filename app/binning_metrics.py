"""Метрики биннинга / скоринга: WoE, IV, KS, Gini для бинарного таргета."""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


_TARGET_EXACT = frozenset(
    {
        "target",
        "y",
        "label",
        "bad",
        "default",
        "is_default",
        "default_flag",
        "fraud",
        "is_fraud",
        "fraud_flag",
        "response",
        "event",
    }
)


def guess_target_column(df: pd.DataFrame) -> str | None:
    """Эвристика: имя колонки похоже на бинарный таргет (дефолт, фрод и т.д.)."""
    best: tuple[int, str] | None = None
    for col in df.columns:
        raw = str(col).strip()
        key = raw.lower()
        if key in _TARGET_EXACT:
            return col
        score = 0
        for token in ("default", "fraud", "delinq", "target", "bad", "flag", "label", "response"):
            if token in key:
                score += 2
        if re.search(r"\b(pd|lgd|ead)\b", key):
            score += 1
        if score and (best is None or score > best[0]):
            best = (score, col)
    return best[1] if best and best[0] >= 2 else None


def _to_binary_positive_is_bad(s: pd.Series) -> pd.Series | None:
    """Возвращает float Series 0/1 (1 = «плохие») или None, если не получилось."""
    if s is None or len(s) == 0:
        return None
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().sum() >= 0.9 * len(s):
        u = pd.Series(num.dropna().unique())
        if len(u) == 2:
            lo, hi = float(u.min()), float(u.max())
            if lo >= 0 and hi <= 1 and hi - lo > 0.5:
                return num.fillna(np.nan)
            return (num == hi).astype(float)
        if len(u) <= 6 and len(u) > 2:
            return None
    st = s.astype("string").str.strip().str.lower()
    mapping: dict[str, float] = {}
    for a, b in (
        ("yes", 1.0),
        ("y", 1.0),
        ("true", 1.0),
        ("1", 1.0),
        ("bad", 1.0),
        ("default", 1.0),
        ("fraud", 1.0),
        ("no", 0.0),
        ("n", 0.0),
        ("false", 0.0),
        ("0", 0.0),
        ("good", 0.0),
    ):
        mapping[a] = b
    mapped = st.map(mapping)
    if mapped.notna().mean() >= 0.85:
        return pd.to_numeric(mapped, errors="coerce")
    if st.nunique(dropna=True) == 2:
        cats = sorted(st.dropna().unique())
        # лексикографически «больший» — условно bad (слабая эвристика)
        hi = cats[-1]
        return (st == hi).astype(float)
    return None


def woe_iv_table(feature: pd.Series, target_bad: pd.Series) -> pd.DataFrame:
    """Таблица по категориям признака: counts, WoE, IV-компонента."""
    mask = target_bad.notna() & (~pd.isna(target_bad))
    f = feature.astype("string").where(mask, pd.NA)
    y = target_bad.where(mask, np.nan)
    df = pd.DataFrame({"f": f, "y": y}).dropna()
    if df.empty:
        return pd.DataFrame()
    total_bad = float(df["y"].sum())
    total_good = float(len(df) - total_bad)
    if total_bad <= 0 or total_good <= 0:
        return pd.DataFrame()

    grp = df.groupby("f", dropna=False).agg(bad=("y", "sum"), n=("y", "count")).reset_index()
    grp = grp.rename(columns={"f": "bin"})
    grp["good"] = grp["n"] - grp["bad"]
    eps = 1e-6
    grp["pct_bad"] = grp["bad"] / total_bad
    grp["pct_good"] = grp["good"] / total_good
    grp["woe"] = np.log((grp["pct_bad"] + eps) / (grp["pct_good"] + eps))
    grp["iv_piece"] = (grp["pct_bad"] - grp["pct_good"]) * grp["woe"]
    grp["bad_rate"] = grp["bad"] / grp["n"]
    grp = grp.sort_values("woe", ascending=False).reset_index(drop=True)
    return grp


def total_iv(woe_df: pd.DataFrame) -> float:
    if woe_df.empty or "iv_piece" not in woe_df.columns:
        return float("nan")
    return float(woe_df["iv_piece"].sum())


def ks_from_woe_table(woe_df: pd.DataFrame) -> float:
    """KS в долях (0..1): макс |кумулятивная доля bad − доля good| по бинам, отсортированным по WoE."""
    if woe_df.empty or "pct_bad" not in woe_df.columns:
        return float("nan")
    d = woe_df.sort_values("woe", ascending=True).reset_index(drop=True)
    cum_b = d["pct_bad"].cumsum()
    cum_g = d["pct_good"].cumsum()
    return float((cum_b - cum_g).abs().max())


def roc_auc_gini(y_bad: pd.Series, score: pd.Series) -> tuple[float, float]:
    """AUC ROC и Gini = 2*AUC−1; score — чем выше, тем выше риск (как непрерывный рейтинг)."""
    m = y_bad.notna() & score.notna()
    y = y_bad[m].astype(float).values
    s = score[m].astype(float).values
    if len(y) < 10 or y.min() == y.max():
        return float("nan"), float("nan")
    order = np.argsort(s)
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1, dtype=float)
    n1 = float(y.sum())
    n0 = len(y) - n1
    if n1 <= 0 or n0 <= 0:
        return float("nan"), float("nan")
    sum_ranks_bad = ranks[y == 1.0].sum()
    auc = (sum_ranks_bad - n1 * (n1 + 1) / 2) / (n0 * n1)
    auc = max(0.0, min(1.0, auc))
    gini = 2 * auc - 1
    return float(auc), float(gini)


def binning_analysis_block(
    df: pd.DataFrame,
    *,
    source_col: str,
    binned_col: str,
    target_col: str | None,
) -> dict[str, Any]:
    """Сводка по одному биннингу: таблица WoE (если есть таргет), IV, KS, Gini по сырому числу."""
    out: dict[str, Any] = {
        "source_col": source_col,
        "binned_col": binned_col,
        "target_col": target_col,
        "woe_table": None,
        "iv": float("nan"),
        "ks": float("nan"),
        "auc": float("nan"),
        "gini": float("nan"),
    }
    if binned_col not in df.columns:
        return out
    feat_cat = df[binned_col]
    if target_col and target_col in df.columns:
        y = _to_binary_positive_is_bad(df[target_col])
        if y is not None:
            wdf = woe_iv_table(feat_cat, y)
            if not wdf.empty:
                out["woe_table"] = wdf
                out["iv"] = total_iv(wdf)
                out["ks"] = ks_from_woe_table(wdf)
            if source_col in df.columns:
                raw = pd.to_numeric(df[source_col], errors="coerce")
                auc, gini = roc_auc_gini(y, raw)
                out["auc"], out["gini"] = auc, gini
    return out
