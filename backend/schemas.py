from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GenerateLayoutRequest(BaseModel):
    protocol: str
    provider: str = "OpenRouter"
    model: str = "openrouter/free"
    api_key: str | None = None


class InterpretCorrectionRequest(BaseModel):
    experiment_id: str
    command: str
    provider: str = "OpenRouter"
    model: str = "openrouter/free"
    api_key: str | None = None


class ApplyCorrectionRequest(BaseModel):
    experiment_id: str
    command: str | None = None
    operations: list[dict[str, Any]] | None = None


class ValidateRequest(BaseModel):
    experiment_id: str


class ManualWellEditRequest(BaseModel):
    experiment_id: str
    original_well: str
    well: str
    label: str
    sample: str = ""
    condition: str = ""
    replicate: int = Field(default=1, ge=1)
    assay_type: str = ""


class ManualSourceEditRequest(BaseModel):
    experiment_id: str
    liquid_name: str
    liquid_role: str = ""
    original_source_labware: str
    original_source_well: str
    source_labware: str
    source_well: str
    available_volume_ul: float = Field(ge=0)


class ManualVolumeUpdate(BaseModel):
    well: str
    liquid_name: str
    liquid_role: str = ""
    source_labware: str
    source_well: str
    volume_ul: float = Field(ge=0)


class ManualVolumeEditRequest(BaseModel):
    experiment_id: str
    updates: list[ManualVolumeUpdate]


class ExportRequest(BaseModel):
    experiment_id: str
    filename: str = "labmate_experiment.xlsx"


class ExperimentResponse(BaseModel):
    experiment_id: str
    state: dict[str, Any] = Field(default_factory=dict)
