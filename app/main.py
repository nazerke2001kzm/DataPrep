"""FastAPI: умный агент планирует очистку/валидацию/биннинг; сервис возвращает готовый файл."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.chat_models import ChatRequest, ChatResponse
from app.domain_presets import PRESET_LABELS_RU, FilePreset
from app.executor import apply_plan
from app.transition_analysis import resolve_transition_plan, run_transition_econometrics
from app.transition_io import load_transition_workbook
from app.llm import agent_chat_reply, planner_prompt, plan_with_claude
from app.prep_context import build_processing_context, context_header_value
from app.user_guidance import guidance_summary_dict, parse_user_guidance
from app.word_report import build_word_report_bytes

app = FastAPI(title="Dataset prep agent", version="1.0.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_frame(filename: str, raw: bytes, *, preset: FilePreset | None = None) -> pd.DataFrame:
    lower = filename.lower()
    if preset == FilePreset.credit_transition_matrix:
        try:
            return load_transition_workbook(filename, raw)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, detail=f"Не удалось прочитать файл матрицы переходов: {exc}") from exc
    buf = io.BytesIO(raw)
    if lower.endswith(".csv"):
        return pd.read_csv(buf)
    if lower.endswith((".xlsx", ".xlsm")):
        return pd.read_excel(buf)
    raise HTTPException(400, detail="Поддерживаются только .csv, .xlsx, .xlsm")


@app.post("/preview-dataset")
async def preview_dataset(
    file: UploadFile = File(...),
    sample_rows: int = Query(10, ge=1, le=20, description="Число первых строк в sample_rows"),
):
    """Превью загруженного файла: колонки, типы, число строк, первые строки, счётчики пропусков (без вызова LLM)."""
    contents = await file.read()
    if not contents:
        raise HTTPException(400, detail="Пустой файл")
    fname = file.filename or "data.csv"
    try:
        df = _load_frame(fname, contents)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=f"Не удалось прочитать таблицу: {exc}") from exc

    n = len(df)
    null_counts = df.isna().sum()
    dtypes = {str(c): str(dtype) for c, dtype in df.dtypes.items()}
    head = df.head(sample_rows)
    try:
        sample_rows_json = json.loads(head.to_json(orient="records", date_format="iso"))
    except Exception:  # noqa: BLE001
        sample_rows_json = head.astype(object).where(pd.notna(head), None).to_dict(orient="records")

    return {
        "filename": fname,
        "row_count": n,
        "column_count": len(df.columns),
        "columns": [str(c) for c in df.columns],
        "dtypes": dtypes,
        "null_counts": {str(k): int(v) for k, v in null_counts.items()},
        "null_rates": {str(k): (float(v) / n if n else 0.0) for k, v in null_counts.items()},
        "sample_rows": sample_rows_json,
        "sample_row_count": len(sample_rows_json),
    }


@app.get("/", include_in_schema=False)
def root():
    """Главная — веб-интерфейс загрузки; API остаётся на /prepare-dataset."""
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/file-presets")
def file_presets():
    """Список пресетов для POST /prepare-dataset?preset=..."""
    return [
        {"id": p.value, "label": PRESET_LABELS_RU[p.value]}
        for p in (
            FilePreset.antifraud,
            FilePreset.legal_entity_scoring,
            FilePreset.individual_scoring,
            FilePreset.credit_transition_matrix,
        )
    ]


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Диалог с агентом; опционально с контекстом последней обработки файла с клиента."""
    try:
        hist = [t.model_dump() for t in req.history]
        reply = agent_chat_reply(
            hist,
            req.message,
            processing_context=req.processing_context,
        )
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail=f"Ошибка модели: {exc}") from exc
    return ChatResponse(reply=reply)


def _report_payload(plan_json: dict[str, Any], exec_log: list[str]) -> dict[str, Any]:
    return {
        **plan_json,
        "execution_log_ru": exec_log,
    }


@app.post("/prepare-dataset")
async def prepare_dataset(
    file: UploadFile = File(...),
    user_guidance: str | None = Form(
        None,
        description="JSON: column_descriptions, validation_rules, freeform_instructions",
    ),
    preset: FilePreset = Query(
        FilePreset.antifraud,
        description="Тип модели/файла: antifraud | legal_entity_scoring | individual_scoring | credit_transition_matrix",
    ),
    preview_only: bool = Query(False, description="Только JSON-план без изменения данных"),
    bundle: bool = Query(False, description="ZIP: prepared.csv + report.json"),
    docx_report: bool = Query(
        False,
        description="Вместе с bundle: добавить report.docx (графики, WoE/IV/KS/Gini по биннингу)",
    ),
):
    if docx_report and not bundle:
        raise HTTPException(
            400,
            detail="Параметр docx_report=true работает только вместе с bundle=true (ZIP-архив).",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(400, detail="Пустой файл")

    try:
        guidance = parse_user_guidance(user_guidance)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    df = _load_frame(file.filename or "data.csv", contents, preset=preset)
    preview = df.head(50).to_csv(index=False)
    columns_info = df.dtypes.to_string()
    null_rates = (df.isna().mean()).to_dict()
    null_rates = {str(k): float(v) if v == v else 0.0 for k, v in null_rates.items()}

    prompt = planner_prompt(
        file.filename or "data.csv",
        columns_info,
        preview,
        null_rates,
        len(df),
        preset=preset,
        user_guidance=guidance,
    )

    try:
        plan = plan_with_claude(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail=f"LLM недоступен или неверный JSON: {exc}") from exc

    if preset == FilePreset.credit_transition_matrix:
        resolved_tm = resolve_transition_plan(df, plan)
        plan = plan.model_copy(update={"transition_matrix": resolved_tm})

    preset_label = PRESET_LABELS_RU.get(preset.value, preset.value)
    fname = file.filename or "data.csv"
    col_names = [str(c) for c in df.columns]

    if preview_only:
        preview_body = json.loads(plan.model_dump_json())
        preview_body["preset_applied"] = preset.value
        plan_dict_pre = json.loads(plan.model_dump_json())
        report_pre = _report_payload(plan_dict_pre, [])
        report_pre["preset_applied"] = preset.value
        if guidance_summary := guidance_summary_dict(guidance):
            report_pre["user_guidance_applied"] = guidance_summary
        if preset == FilePreset.credit_transition_matrix and plan.transition_matrix:
            try:
                report_pre["transition_econometrics"] = run_transition_econometrics(
                    df, plan.transition_matrix
                )
            except Exception as exc:  # noqa: BLE001
                report_pre["transition_econometrics_error"] = str(exc)
        ctx_pre = build_processing_context(
            filename=fname,
            preset_value=preset.value,
            preset_label_ru=preset_label,
            rows_before=len(df),
            rows_after=None,
            column_names=col_names,
            report=report_pre,
            preview_plan_only=True,
            user_guidance=guidance,
        )
        return JSONResponse(
            preview_body,
            headers={"X-Prep-Context": context_header_value(ctx_pre)},
        )

    prepared, exec_log = apply_plan(df, plan, user_guidance=guidance)

    stem = fname.rsplit(".", 1)[0]
    out_base = f"{stem}_{preset.value}_prepared"
    plan_dict = json.loads(plan.model_dump_json())
    report = _report_payload(plan_dict, exec_log)
    report["preset_applied"] = preset.value
    if guidance_summary := guidance_summary_dict(guidance):
        report["user_guidance_applied"] = guidance_summary
    if preset == FilePreset.credit_transition_matrix and plan.transition_matrix:
        try:
            report["transition_econometrics"] = run_transition_econometrics(
                prepared, plan.transition_matrix
            )
        except Exception as exc:  # noqa: BLE001
            report["transition_econometrics_error"] = str(exc)

    ctx_chat = build_processing_context(
        filename=fname,
        preset_value=preset.value,
        preset_label_ru=preset_label,
        rows_before=len(df),
        rows_after=len(prepared),
        column_names=col_names,
        report=report,
        preview_plan_only=False,
        user_guidance=guidance,
    )
    ctx_hdr = {"X-Prep-Context": context_header_value(ctx_chat)}

    csv_buf = io.StringIO()
    prepared.to_csv(csv_buf, index=False)
    csv_bytes = csv_buf.getvalue().encode("utf-8")

    if bundle:
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("prepared.csv", csv_bytes)
            zf.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
            if docx_report:
                try:
                    docx_bytes = build_word_report_bytes(
                        filename=fname,
                        preset_label=preset_label,
                        rows_before=len(df),
                        rows_after=len(prepared),
                        df_original=df,
                        df_prepared=prepared,
                        report=report,
                        plan=plan,
                    )
                    zf.writestr("report.docx", docx_bytes)
                except Exception as exc:  # noqa: BLE001
                    zf.writestr(
                        "report_docx_error.txt",
                        f"Не удалось сформировать Word-отчёт: {exc}\n".encode("utf-8"),
                    )
        return Response(
            content=zbuf.getvalue(),
            media_type="application/zip",
            headers={**ctx_hdr, "Content-Disposition": f'attachment; filename="{out_base}.zip"'},
        )

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={**ctx_hdr, "Content-Disposition": f'attachment; filename="{out_base}.csv"'},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
