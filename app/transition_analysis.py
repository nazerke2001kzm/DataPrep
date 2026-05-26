"""Матрица переходов: преобразования макрофакторов, стационарность, VIF, нормальность, лаги."""

from __future__ import annotations

import re
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.schemas import AnalystReport, FeatureTransformSpec, TransitionMatrixPlan
from app.transition_io import (
    guess_kz_macro_columns,
    guess_transition_rate_columns,
    normalize_quarter_label,
    validate_transition_row_sums,
)

TransformKind = Literal["qoq_abs", "qoq_rel", "log", "zscore"]

_TRANSFORM_SUFFIX: dict[TransformKind, str] = {
    "qoq_abs": "_qoq_abs",
    "qoq_rel": "_qoq_rel",
    "log": "_log",
    "zscore": "_z",
}

_PERIOD_TOKENS = ("period", "quarter", "квартал", "date", "дата", "year", "год", "time")
_BUCKET_TOKENS = (
    "bucket",
    "корзин",
    "rating",
    "рейтинг",
    "from_bucket",
    "to_bucket",
    "grade",
    "class",
    "segment",
)
_MACRO_TOKENS = (
    "macro",
    "gdp",
    "ввп",
    "inflation",
    "инфляц",
    "rate",
    "ставк",
    "unemp",
    "безработ",
    "ip_",
    "industrial",
    "пром",
    "cpi",
    "ипц",
    "fx",
    "курс",
    "spread",
    "oil",
    "нефт",
)


def _col_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(name).lower())


def guess_period_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        k = _col_key(col)
        if any(t in k for t in _PERIOD_TOKENS):
            return str(col)
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return str(col)
    return None


def guess_bucket_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        k = _col_key(col)
        if any(t in k for t in _BUCKET_TOKENS):
            if df[col].nunique(dropna=True) <= max(30, len(df) // 5):
                return str(col)
    return None


def guess_macro_columns(df: pd.DataFrame, exclude: set[str]) -> list[str]:
    kz = [c for c in guess_kz_macro_columns(df) if c not in exclude]
    if kz:
        return kz
    out: list[str] = []
    for col in df.columns:
        if str(col) in exclude:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            ser = pd.to_numeric(df[col], errors="coerce")
            if ser.notna().sum() < 0.5 * len(df):
                continue
        else:
            ser = df[col]
        k = _col_key(col)
        if any(t in k for t in _MACRO_TOKENS):
            out.append(str(col))
            continue
        if ser.notna().sum() >= 8 and ser.nunique(dropna=True) >= 5:
            if not any(t in k for t in ("id", "count", "cnt", "index", "row")):
                out.append(str(col))
    return out[:25]


def guess_dependent_column(df: pd.DataFrame, exclude: set[str]) -> str | None:
    tr = [c for c in guess_transition_rate_columns(df) if c not in exclude]
    if tr:
        return tr[0]
    for col in df.columns:
        if str(col) in exclude:
            continue
        k = _col_key(col)
        if any(t in k for t in ("transition", "migrate", "переход", "prob", "share", "доля", "rate", "pd")):
            if pd.to_numeric(df[col], errors="coerce").notna().sum() >= 5:
                return str(col)
    numeric = [
        c
        for c in df.columns
        if str(c) not in exclude and pd.to_numeric(df[c], errors="coerce").notna().sum() >= 8
    ]
    return str(numeric[0]) if numeric else None


def resolve_transition_plan(df: pd.DataFrame, plan: AnalystReport) -> TransitionMatrixPlan:
    tm = plan.transition_matrix
    if tm is None:
        tm = TransitionMatrixPlan()

    period = tm.period_column or guess_period_column(df) or (
        "period" if "period" in df.columns else ("_period_key" if "_period_key" in df.columns else None)
    )
    bucket = tm.bucket_column or (
        "basket_tr" if "basket_tr" in df.columns else guess_bucket_column(df)
    )
    tr_cols = list(tm.transition_rate_columns) if tm.transition_rate_columns else guess_transition_rate_columns(df)
    tr_cols = [c for c in tr_cols if c in df.columns]

    exclude = {x for x in (period, bucket, "_period_key", "product_tr") if x}
    exclude |= set(tr_cols)

    macros = list(tm.macro_columns) if tm.macro_columns else guess_kz_macro_columns(df)
    if not macros:
        macros = guess_macro_columns(df, exclude)
    macros = [c for c in macros if c in df.columns]

    dep = tm.dependent_column
    if not dep and tr_cols:
        dep = tr_cols[0]
    if not dep:
        dep = guess_dependent_column(df, exclude | set(macros))

    product = tm.product_label or (
        str(df["basket_tr"].iloc[0]) if "basket_tr" in df.columns and len(df) else "Денежный 1"
    )

    transforms = list(tm.feature_transforms)
    default_kinds: list[TransformKind] = ["qoq_abs", "qoq_rel", "log", "zscore"]
    if not transforms:
        cols_for_tf: list[str] = []
        if tm.transform_macro is not False:
            cols_for_tf.extend(macros)
        if tm.transform_targets is not False:
            cols_for_tf.extend(tr_cols)
        transforms = [FeatureTransformSpec(column=c, transforms=default_kinds) for c in cols_for_tf[:20]]

    return TransitionMatrixPlan(
        bucket_column=bucket,
        period_column=period,
        product_label=product,
        macro_columns=macros,
        transition_rate_columns=tr_cols,
        dependent_column=dep,
        transform_macro=tm.transform_macro,
        transform_targets=tm.transform_targets,
        feature_transforms=transforms,
    )


def _order_frame(df: pd.DataFrame, tm: TransitionMatrixPlan) -> pd.DataFrame:
    out = df.copy()
    sort_cols: list[str] = []
    if tm.bucket_column and tm.bucket_column in out.columns:
        sort_cols.append(tm.bucket_column)
    if tm.period_column and tm.period_column in out.columns:
        pc = tm.period_column
        if "_period_key" not in out.columns:
            out["_period_key"] = out[pc].map(normalize_quarter_label)
        if "_period_key" in out.columns:
            sort_cols.append("_period_key")
        elif not pd.api.types.is_datetime64_any_dtype(out[pc]):
            out[pc] = pd.to_datetime(out[pc], errors="coerce")
            if out[pc].isna().all():
                out[pc] = pd.to_numeric(df[pc], errors="coerce")
            sort_cols.append(pc)
    if sort_cols:
        out = out.sort_values(sort_cols).reset_index(drop=True)
    return out


def apply_transition_features(
    df: pd.DataFrame,
    tm: TransitionMatrixPlan,
) -> tuple[pd.DataFrame, list[str]]:
    """Квартальные приросты, log, z-score для макрофакторов и корзины (если числовая)."""
    out = _order_frame(df, tm)
    log: list[str] = []

    if tm.bucket_column and tm.bucket_column in out.columns:
        try:
            out[tm.bucket_column] = out[tm.bucket_column].astype("category")
            log.append(f"Корзина «{tm.bucket_column}» приведена к category (готовая корзина)")
        except Exception as exc:  # noqa: BLE001
            log.append(f"Корзина «{tm.bucket_column}»: {exc}")

    group_keys = [tm.bucket_column] if tm.bucket_column and tm.bucket_column in out.columns else []

    specs = list(tm.feature_transforms)
    if not specs and tm.macro_columns:
        specs = [
            FeatureTransformSpec(
                column=c,
                transforms=["qoq_abs", "qoq_rel", "log", "zscore"],
            )
            for c in tm.macro_columns
            if c in out.columns
        ]

    for spec in specs:
        col = spec.column
        if col not in out.columns:
            continue
        base = pd.to_numeric(out[col], errors="coerce")

        def _group_apply(ser: pd.Series, fn) -> pd.Series:
            if group_keys:
                return ser.groupby(out[group_keys[0]], observed=True).transform(fn)
            return fn(ser)

        for kind in spec.transforms:
            suffix = _TRANSFORM_SUFFIX.get(kind)
            if not suffix:
                continue
            out_col = f"{col}{suffix}"
            try:
                if kind == "qoq_abs":
                    out[out_col] = _group_apply(base, lambda s: s.diff(1))
                elif kind == "qoq_rel":
                    out[out_col] = _group_apply(base, lambda s: s.pct_change(1))
                elif kind == "log":
                    pos = base.where(base > 0)
                    out[out_col] = np.log(pos)
                    out[out_col] = out[out_col].where(pos.notna())
                elif kind == "zscore":
                    if group_keys:
                        out[out_col] = base.groupby(out[group_keys[0]], observed=True).transform(
                            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) not in (0, np.nan) else np.nan
                        )
                    else:
                        std = base.std(ddof=0)
                        out[out_col] = (base - base.mean()) / std if std and std == std else np.nan
                log.append(f"«{col}» → «{out_col}» ({kind})")
            except Exception as exc:  # noqa: BLE001
                log.append(f"«{col}» transform {kind}: пропуск — {exc}")

    return out, log


def _series_for_tests(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _adf(series: pd.Series) -> dict[str, Any]:
    from statsmodels.tsa.stattools import adfuller

    s = series.astype(float)
    if len(s) < 8:
        return {"ok": False, "reason": "мало наблюдений"}
    maxlag = min(12, max(1, len(s) // 4))
    stat, pval, *_ = adfuller(s, autolag="AIC", maxlag=maxlag)
    return {
        "ok": True,
        "statistic": float(stat),
        "pvalue": float(pval),
        "stationary_5pct": bool(pval < 0.05),
        "interpretation_ru": "стационарен (отвергаем H0 единичного корня)"
        if pval < 0.05
        else "не стационарен на 5%",
    }


def _kpss(series: pd.Series) -> dict[str, Any]:
    from statsmodels.tsa.stattools import kpss

    s = series.astype(float)
    if len(s) < 8:
        return {"ok": False, "reason": "мало наблюдений"}
    stat, pval, lags, crit = kpss(s, regression="c", nlags="auto")
    return {
        "ok": True,
        "statistic": float(stat),
        "pvalue": float(pval),
        "lags": int(lags),
        "stationary_5pct": bool(pval > 0.05),
        "interpretation_ru": "стационарен (не отвергаем H0 стационарности KPSS)"
        if pval > 0.05
        else "не стационарен на 5% (отвергаем H0 стационарности)",
    }


def _phillips_perron(series: pd.Series) -> dict[str, Any]:
    try:
        from arch.unitroot import PhillipsPerron
    except ImportError:
        return {"ok": False, "reason": "установите пакет arch для PP-теста"}
    s = series.astype(float)
    if len(s) < 8:
        return {"ok": False, "reason": "мало наблюдений"}
    pp = PhillipsPerron(s, lags=None)
    pval = float(pp.pvalue) if pp.pvalue is not None else float("nan")
    return {
        "ok": True,
        "statistic": float(pp.stat),
        "pvalue": pval,
        "stationary_5pct": bool(pval < 0.05) if pval == pval else False,
        "interpretation_ru": "стационарен (PP)" if pval < 0.05 else "не стационарен (PP) на 5%",
    }


def _normality(series: pd.Series) -> dict[str, Any]:
    from scipy.stats import jarque_bera, shapiro

    s = series.astype(float)
    n = len(s)
    if n < 8:
        return {"ok": False, "reason": "мало наблюдений"}
    jb_stat, jb_p = jarque_bera(s)
    out: dict[str, Any] = {
        "ok": True,
        "jarque_bera": {"statistic": float(jb_stat), "pvalue": float(jb_p), "normal_5pct": bool(jb_p > 0.05)},
    }
    if n <= 5000:
        sw_stat, sw_p = shapiro(s)
        out["shapiro"] = {
            "statistic": float(sw_stat),
            "pvalue": float(sw_p),
            "normal_5pct": bool(sw_p > 0.05),
        }
    else:
        out["shapiro"] = {"skipped": True, "reason": "n>5000"}
    normal = out["jarque_bera"]["normal_5pct"]
    if "pvalue" in out.get("shapiro", {}):
        normal = normal and out["shapiro"]["normal_5pct"]
    out["summary_ru"] = "близко к нормальному на 5%" if normal else "отклонение от нормальности на 5%"
    return out


def _vif_table(df: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    cols = [c for c in columns if c in df.columns]
    if len(cols) < 2:
        return {"ok": False, "reason": "нужно ≥2 предикторов"}
    X = df[cols].apply(pd.to_numeric, errors="coerce").dropna()
    if len(X) < len(cols) + 3:
        return {"ok": False, "reason": "мало строк после dropna"}
    X = sm.add_constant(X, has_constant="add")
    names = list(X.columns)
    rows: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        if name == "const":
            continue
        try:
            vif = float(variance_inflation_factor(X.values, i))
        except Exception as exc:  # noqa: BLE001
            vif = float("nan")
            rows.append({"variable": name, "vif": vif, "flag": f"ошибка: {exc}"})
            continue
        flag = "сильная мультиколлинеарность" if vif > 10 else ("умеренная" if vif > 5 else "приемлемо")
        rows.append({"variable": name, "vif": round(vif, 4) if vif == vif else None, "flag": flag})
    high = [r["variable"] for r in rows if isinstance(r.get("vif"), (int, float)) and r["vif"] > 10]
    return {
        "ok": True,
        "rows": rows,
        "high_vif_variables": high,
        "summary_ru": "есть VIF>10 — рассмотрите исключение или PCA"
        if high
        else "мультиколлинеарность в пределах нормы (VIF≤10)",
    }


def _adjusted_r2_ranking(
    df: pd.DataFrame,
    y_col: str,
    predictors: list[str],
) -> dict[str, Any]:
    import statsmodels.api as sm

    y = pd.to_numeric(df[y_col], errors="coerce")
    rows: list[dict[str, Any]] = []
    for col in predictors:
        if col not in df.columns or col == y_col:
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        mask = y.notna() & x.notna()
        if mask.sum() < 8:
            continue
        X = sm.add_constant(x[mask])
        try:
            model = sm.OLS(y[mask], X).fit()
            rows.append(
                {
                    "predictor": col,
                    "adj_r2": round(float(model.rsquared_adj), 6),
                    "n_obs": int(mask.sum()),
                }
            )
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda r: r["adj_r2"], reverse=True)
    selected = [r["predictor"] for r in rows[:8] if r["adj_r2"] > 0]
    return {
        "ok": bool(rows),
        "dependent": y_col,
        "ranking": rows,
        "suggested_predictors": selected,
        "summary_ru": "топ по adj. R²: " + ", ".join(selected[:5]) if selected else "не удалось ранжировать",
    }


def _lag_suggestions(series: pd.Series, stationarity: dict[str, Any]) -> dict[str, Any]:
    from statsmodels.tsa.stattools import acf, pacf

    s = series.astype(float).dropna()
    n = len(s)
    if n < 12:
        return {"ok": False, "reason": "мало наблюдений для ACF/PACF"}
    max_lag = min(8, n // 3)
    acf_vals = acf(s, nlags=max_lag, fft=True, missing="drop")
    pacf_vals = pacf(s, nlags=max_lag, method="ywm")
    thresh = 2 / np.sqrt(n)
    pacf_lags = [i for i in range(1, len(pacf_vals)) if abs(pacf_vals[i]) > thresh]
    acf_lags = [i for i in range(1, len(acf_vals)) if abs(acf_vals[i]) > thresh]

    adf_st = stationarity.get("adf", {})
    non_stat = not adf_st.get("stationary_5pct", False) if adf_st.get("ok") else True

    suggested: list[int] = []
    if non_stat:
        suggested.extend([1, 2, 4])
    suggested.extend(pacf_lags[:4])
    suggested = sorted({int(x) for x in suggested if 0 < x <= 8})[:6]

    notes: list[str] = []
    if non_stat:
        notes.append("ряд нестационарен (ADF): рассмотрите дифференцирование или лаги 1–4")
    if pacf_lags:
        notes.append(f"значимые PACF на лагах: {pacf_lags}")
    if not suggested:
        suggested = [1]
        notes.append("по умолчанию предложен лаг 1")

    return {
        "ok": True,
        "pacf_significant_lags": pacf_lags,
        "acf_significant_lags": acf_lags,
        "suggested_lags": suggested,
        "notes_ru": notes,
        "summary_ru": f"рекомендуемые лаги: {suggested}",
    }


def run_transition_econometrics(
    df: pd.DataFrame,
    tm: TransitionMatrixPlan,
) -> dict[str, Any]:
    """Стационарность, VIF, нормальность, adj. R², лаги — для отчёта и чата."""
    cols_to_test: list[str] = []
    for c in tm.macro_columns:
        if c in df.columns:
            cols_to_test.append(c)
    for spec in tm.feature_transforms:
        col = spec.column
        if col not in df.columns:
            continue
        for kind in spec.transforms:
            suf = _TRANSFORM_SUFFIX.get(kind)
            if suf:
                derived = f"{col}{suf}"
                if derived in df.columns:
                    cols_to_test.append(derived)
    cols_to_test = list(dict.fromkeys(cols_to_test))

    targets = [c for c in (tm.transition_rate_columns or []) if c in df.columns]
    if not targets and tm.dependent_column and tm.dependent_column in df.columns:
        targets = [tm.dependent_column]
    for tcol in targets:
        if tcol not in cols_to_test:
            cols_to_test.append(tcol)

    stationarity: dict[str, Any] = {}
    normality: dict[str, Any] = {}
    lags_by_series: dict[str, Any] = {}

    for col in cols_to_test[:40]:
        s = _series_for_tests(df, col)
        if len(s) < 8:
            continue
        st = {
            "adf": _adf(s),
            "kpss": _kpss(s),
            "pp": _phillips_perron(s),
        }
        votes = sum(
            1
            for t in (st["adf"], st["kpss"], st["pp"])
            if t.get("ok") and t.get("stationary_5pct")
        )
        tests_ok = sum(1 for t in (st["adf"], st["kpss"], st["pp"]) if t.get("ok"))
        st["consensus_stationary"] = votes >= 2 if tests_ok >= 2 else None
        stationarity[col] = st
        normality[col] = _normality(s)
        lags_by_series[col] = _lag_suggestions(s, st)

    vif_cols = [c for c in cols_to_test if c in df.columns][:20]
    vif = _vif_table(df, vif_cols)

    adj_r2: dict[str, Any] = {"ok": False}
    if tm.dependent_column and tm.dependent_column in df.columns:
        adj_r2 = _adjusted_r2_ranking(df, tm.dependent_column, vif_cols)

    adj_r2_by_target: dict[str, Any] = {}
    for tcol in targets[:9]:
        block = _adjusted_r2_ranking(df, tcol, vif_cols)
        if block.get("ok"):
            adj_r2_by_target[tcol] = block

    tr_validation = validate_transition_row_sums(df, targets) if targets else []

    global_lags: list[int] = []
    for info in lags_by_series.values():
        if info.get("ok"):
            global_lags.extend(info.get("suggested_lags", []))
    global_lags = sorted({int(x) for x in global_lags})[:8]

    recommendations_ru: list[str] = []
    if vif.get("high_vif_variables"):
        recommendations_ru.append(
            f"Снизить мультиколлинеарность: высокий VIF у {', '.join(vif['high_vif_variables'][:5])}"
        )
    if adj_r2.get("suggested_predictors"):
        recommendations_ru.append(
            f"Для модели с «{tm.dependent_column}» приоритет по adj. R²: "
            + ", ".join(adj_r2["suggested_predictors"][:5])
        )
    non_stat_cols = [c for c, st in stationarity.items() if st.get("consensus_stationary") is False]
    if non_stat_cols:
        recommendations_ru.append(
            "Нестационарные ряды (консенсус тестов): "
            + ", ".join(non_stat_cols[:6])
            + " — используйте приросты (_qoq_abs/_qoq_rel) или лаги"
        )
    if global_lags:
        recommendations_ru.append(f"Ориентир по лагам AR/VAR: {global_lags}")
    if tr_validation:
        recommendations_ru.extend(tr_validation[:4])
    if adj_r2_by_target:
        best = max(
            adj_r2_by_target.items(),
            key=lambda kv: (kv[1].get("ranking") or [{}])[0].get("adj_r2", -1)
            if kv[1].get("ranking")
            else -1,
        )
        recommendations_ru.append(
            f"Пример: для таргета «{best[0]}» сильнее всего макро (adj. R²): "
            + ", ".join((best[1].get("suggested_predictors") or [])[:3])
        )

    return {
        "product_label": tm.product_label,
        "bucket_column": tm.bucket_column,
        "period_column": tm.period_column,
        "macro_columns": tm.macro_columns,
        "transition_rate_columns": tm.transition_rate_columns,
        "dependent_column": tm.dependent_column,
        "transition_row_sum_checks": tr_validation,
        "adjusted_r2_by_target": adj_r2_by_target,
        "columns_analyzed": cols_to_test,
        "stationarity": stationarity,
        "normality": normality,
        "vif": vif,
        "adjusted_r2_ranking": adj_r2,
        "lag_suggestions_by_series": lags_by_series,
        "recommended_lags_global": global_lags,
        "recommendations_ru": recommendations_ru,
    }
