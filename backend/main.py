from __future__ import annotations

from io import BytesIO
import os
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.config import allowed_origins, env_bool, resolve_provider_api_key
from backend.schemas import (
    ApplyCorrectionRequest,
    ExportRequest,
    GenerateLayoutRequest,
    InterpretCorrectionRequest,
    ManualSourceEditRequest,
    ManualVolumeEditRequest,
    ManualWellEditRequest,
    ValidateRequest,
)
from backend.store import create_experiment, get_experiment, update_experiment
from backend.security import DemoSecurityMiddleware, InMemoryRateLimiter
from outputs.excel_writer import build_excel_workbook
from services.correction_service import apply_confirmed_correction, interpret_correction_command
from services.experiment_service import generate_draft_experiment, manually_edit_plate_well, manually_edit_source, manually_edit_volumes
from services.validation_service import run_validation as service_run_validation


docs_enabled = env_bool("ENABLE_DOCS", default=True)
app = FastAPI(
    title="LabMate API",
    version="0.2.0",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)

app.add_middleware(
    DemoSecurityMiddleware,
    rate_limiter=InMemoryRateLimiter(
        limit=int(os.environ.get("RATE_LIMIT_REQUESTS", "30")),
        window_seconds=int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")),
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Demo-Access-Code"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def df_to_records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None:
        return []
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def records_to_df(records: list[dict[str, Any]] | None, columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(records or [])
    if columns is None:
        return frame
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame[columns]


def json_compatible(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return df_to_records(value)
    if isinstance(value, dict):
        return {key: json_compatible(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [json_compatible(item) for item in value]
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def experiment_response(experiment_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "state": json_compatible(state),
    }


def load_experiment(experiment_id: str) -> dict[str, Any]:
    try:
        return get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found.") from exc


@app.get("/api/experiments/{experiment_id}")
def get_experiment_state(experiment_id: str) -> dict[str, Any]:
    return experiment_response(experiment_id, load_experiment(experiment_id))


@app.post("/api/generate-layout")
def generate_layout(request: GenerateLayoutRequest) -> dict[str, Any]:
    try:
        state = generate_draft_experiment(
            request.protocol,
            provider=request.provider,
            model=request.model,
            api_key=resolve_provider_api_key(request.provider, request.api_key),
        )
        experiment_id = create_experiment(state)
        return experiment_response(experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/interpret-correction")
def interpret_correction(request: InterpretCorrectionRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        interpreted = interpret_correction_command(
            request.command,
            provider=request.provider,
            model=request.model,
            api_key=resolve_provider_api_key(request.provider, request.api_key),
            original_protocol=state.get("original_protocol", ""),
            initial_plate_layout=state["initial_plate_layout"],
            initial_well_contents=state["initial_well_contents"],
            initial_source_map=state["initial_source_map"],
            plate_layout=state["plate_layout"],
            well_contents=state["well_contents"],
            source_map=state["source_map"],
            edit_history=state.get("edit_history", []),
            correction_chat=state.get("correction_chat", []),
        )
        state.update(interpreted)
        state["correction_chat"] = state.get("correction_chat", []) + interpreted.get("chat_messages", [])
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/apply-correction")
def apply_correction(request: ApplyCorrectionRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        operations = request.operations if request.operations is not None else state.get("pending_operations", [])
        command = request.command or state.get("last_correction_context", {}).get("latest_user_command", "")
        applied = apply_confirmed_correction(
            plate_layout=state["plate_layout"],
            well_contents=state["well_contents"],
            source_map=state["source_map"],
            spec=state.get("spec", {}),
            operations=operations,
            command=command,
            edit_history=state.get("edit_history", []),
        )
        state.update(applied)
        state["correction_chat"] = state.get("correction_chat", []) + applied.get("chat_messages", [])
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/validate")
def validate(request: ValidateRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        validation = service_run_validation(
            state["plate_layout"],
            state["well_contents"],
            state["source_map"],
            state.get("spec", {}),
        )
        state.update(validation)
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manual-edit-well")
def manual_edit_well(request: ManualWellEditRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        edited = manually_edit_plate_well(
            plate_layout=state["plate_layout"],
            well_contents=state["well_contents"],
            spec=state.get("spec", {}),
            original_well=request.original_well,
            well=request.well,
            label=request.label,
            sample=request.sample,
            condition=request.condition,
            replicate=request.replicate,
            assay_type=request.assay_type,
            edit_history=state.get("edit_history", []),
        )
        state.update(edited)
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manual-edit-source")
def manual_edit_source(request: ManualSourceEditRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        edited = manually_edit_source(
            plate_layout=state["plate_layout"],
            well_contents=state["well_contents"],
            source_map=state["source_map"],
            spec=state.get("spec", {}),
            liquid_name=request.liquid_name,
            liquid_role=request.liquid_role,
            original_source_labware=request.original_source_labware,
            original_source_well=request.original_source_well,
            source_labware=request.source_labware,
            source_well=request.source_well,
            available_volume_ul=request.available_volume_ul,
            edit_history=state.get("edit_history", []),
        )
        state.update(edited)
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manual-edit-volumes")
def manual_edit_volumes(request: ManualVolumeEditRequest) -> dict[str, Any]:
    state = load_experiment(request.experiment_id)
    try:
        edited = manually_edit_volumes(
            plate_layout=state["plate_layout"],
            well_contents=state["well_contents"],
            source_map=state["source_map"],
            spec=state.get("spec", {}),
            updates=[update.model_dump() for update in request.updates],
            edit_history=state.get("edit_history", []),
        )
        state.update(edited)
        update_experiment(request.experiment_id, state)
        return experiment_response(request.experiment_id, state)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/export")
def export(request: ExportRequest) -> StreamingResponse:
    state = load_experiment(request.experiment_id)
    try:
        workbook = build_excel_workbook(
            state["plate_layout"],
            state["well_contents"],
            state["source_map"],
            state["transfer_table"],
            state["validation_report"],
            state["summary_matrix"],
        )
        headers = {"Content-Disposition": f'attachment; filename="{request.filename}"'}
        return StreamingResponse(
            BytesIO(workbook),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
