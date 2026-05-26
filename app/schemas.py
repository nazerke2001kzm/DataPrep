"""Plan emitted by the model; executor applies it deterministically."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class FillNa(BaseModel):
    strategy: Literal["median", "mean", "mode", "constant", "drop_rows"]
    constant: Optional[str | float | int] = None


class NumericOutliers(BaseModel):
    columns: list[str]
    mode: Literal["winsorize_iqr", "drop_iqr"]
    iqr_multiplier: float = Field(default=1.5, ge=0)


class StripWhitespace(BaseModel):
    columns: Optional[list[str]] = None  # None = all object columns


class CleaningPlan(BaseModel):
    dedupe: Literal["none", "full", "subset"] = "none"
    dedupe_columns: list[str] = Field(default_factory=list)
    drop_columns: list[str] = Field(default_factory=list)
    drop_rows_all_na: bool = False
    fill_na: dict[str, FillNa] = Field(default_factory=dict)
    numeric_outliers: Optional[NumericOutliers] = None
    strip_whitespace: Optional[StripWhitespace] = None


class ColumnCast(BaseModel):
    column: str
    dtype: Literal["int64", "float64", "string", "category", "datetime64[ns]", "boolean"]


class NumericClip(BaseModel):
    column: str
    min: Optional[float] = None
    max: Optional[float] = None


class ValidationPlan(BaseModel):
    cast_columns: list[ColumnCast] = Field(default_factory=list)
    numeric_clip: list[NumericClip] = Field(default_factory=list)


ValidationRuleKind = Literal[
    "must_be_numeric",
    "must_be_integer",
    "must_be_datetime",
    "date_year_min",
    "date_year_max",
    "numeric_min",
    "numeric_max",
    "string_length_min",
    "string_length_max",
    "regex_extract_digits",
    "regex_extract",
    "regex_replace",
    "regex_must_match",
    "not_null",
    "allowed_values",
    "unique",
]


class ColumnDescription(BaseModel):
    column: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., max_length=4000)


class ValidationRule(BaseModel):
    """Правило валидации: задаёт пользователь и/или повторяет план LLM."""

    column: str = Field(..., min_length=1, max_length=200)
    rule: ValidationRuleKind
    params: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)
    on_fail: Literal["null", "drop_row", "keep"] = "null"
    note: str = Field(default="", max_length=500)


class UserDatasetGuidance(BaseModel):
    """Справочник колонок и правила — для промпта и детерминированного исполнителя."""

    column_descriptions_text: str = Field(
        default="",
        max_length=16000,
        description="Свободный текст: смысл колонок для ИИ (как freeform)",
    )
    column_descriptions: list[ColumnDescription] = Field(
        default_factory=list,
        description="Устаревший формат; используйте column_descriptions_text",
    )
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    freeform_instructions: list[str] = Field(
        default_factory=list,
        description="Свободные требования пользователя (учитывает LLM в плане)",
    )


class BinSpec(BaseModel):
    column: str
    output_column: str
    method: Literal["quantile", "equal"]
    n_bins: int = Field(ge=2, le=50)
    labels: Optional[list[str]] = Field(
        default=None,
        description="Явные подписи бинов; если null — в output_column пишутся целые номера групп 1…K по порядку интервалов",
    )


class FeatureTransformSpec(BaseModel):
    column: str
    transforms: list[Literal["qoq_abs", "qoq_rel", "log", "zscore"]] = Field(default_factory=list)


class TransitionMatrixPlan(BaseModel):
    """Пресет матрицы переходов: корзина TR, макрофакторы, доли переходов 1>1…3>3."""

    bucket_column: Optional[str] = Field(
        default=None,
        description="Колонка корзины TR (например basket_tr = «Денежный 1»)",
    )
    period_column: Optional[str] = Field(
        default=None,
        description="Квартал period / _period_key (2010Q1 … 2022Q4)",
    )
    product_label: Optional[str] = Field(
        default=None,
        description="Название продукта TR, напр. «Денежный 1»",
    )
    macro_columns: list[str] = Field(
        default_factory=list,
        description="real_gdp, GDD_Min_R, GDD_Elc_R, GDD_Con_R, GDD_Trd_R, GDD_Trn_R",
    )
    transition_rate_columns: list[str] = Field(
        default_factory=list,
        description="Таргеты: 1>1, 1>2, …, 3>3 (доли перехода, %)",
    )
    dependent_column: Optional[str] = Field(
        default=None,
        description="Один таргет для краткого adj. R²; если пусто — все transition_rate_columns",
    )
    transform_macro: bool = Field(default=True)
    transform_targets: bool = Field(
        default=True,
        description="Преобразования также для колонок 1>1…3>3",
    )
    feature_transforms: list[FeatureTransformSpec] = Field(
        default_factory=list,
        description="qoq_abs, qoq_rel, log, zscore → суффиксы _qoq_abs, _qoq_rel, _log, _z",
    )


class AnalystReport(BaseModel):
    summary_ru: str = ""
    cleaning_notes: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    binning_notes: list[str] = Field(default_factory=list)
    econometric_notes: list[str] = Field(
        default_factory=list,
        description="Заметки по стационарности, VIF, лагам (пресет матрицы переходов)",
    )
    cleaning: CleaningPlan = Field(default_factory=CleaningPlan)
    validation: ValidationPlan = Field(default_factory=ValidationPlan)
    custom_validation: list[ValidationRule] = Field(
        default_factory=list,
        description="Доп. правила от модели (дубли с пользовательскими не перезаписывают user)",
    )
    binning: list[BinSpec] = Field(default_factory=list)
    transition_matrix: Optional[TransitionMatrixPlan] = None
