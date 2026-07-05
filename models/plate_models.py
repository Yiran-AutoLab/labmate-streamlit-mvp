from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


ROWS = "ABCDEFGH"
COLUMNS = list(range(1, 13))
VALID_WELLS = [f"{row}{column}" for row in ROWS for column in COLUMNS]


@dataclass
class PlateLayoutRow:
    well: str
    label: str
    sample: str
    condition: str
    replicate: int
    assay_type: str = ""


@dataclass
class WellContentRow:
    well: str
    label: str
    liquid_name: str
    liquid_role: str
    volume_ul: float
    source_labware: str
    source_well: str


@dataclass
class SourceMapRow:
    liquid_name: str
    liquid_role: str
    source_labware: str
    source_well: str
    available_volume_ul: float
    required_volume_ul: float
    status: str


@dataclass
class TransferStep:
    step: int
    source_labware: str
    source_well: str
    destination_labware: str
    destination_well: str
    liquid_name: str
    volume_ul: float


@dataclass
class ValidationResult:
    check_name: str
    status: str
    detail: str
    severity: str


def normalize_well(well: str) -> str:
    return str(well).strip().upper()


def is_valid_well(well: str) -> bool:
    return normalize_well(well) in VALID_WELLS


def next_well_after_avoids(used: set[str], avoided: set[str]) -> str:
    for well in VALID_WELLS:
        if well not in used and well not in avoided:
            return well
    raise ValueError("No available wells left on a 96-well plate.")


def dataclass_rows_to_df(rows: list[Any]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in rows])


def ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = df.copy()
    for column in columns:
        if column not in result.columns:
            result[column] = ""
    return result[columns]


PLATE_LAYOUT_COLUMNS = ["well", "label", "sample", "condition", "replicate", "assay_type"]
WELL_CONTENT_COLUMNS = [
    "well",
    "label",
    "liquid_name",
    "liquid_role",
    "volume_ul",
    "source_labware",
    "source_well",
]
SOURCE_MAP_COLUMNS = [
    "liquid_name",
    "liquid_role",
    "source_labware",
    "source_well",
    "available_volume_ul",
    "required_volume_ul",
    "status",
]
TRANSFER_COLUMNS = [
    "step",
    "source_labware",
    "source_well",
    "destination_labware",
    "destination_well",
    "liquid_name",
    "volume_ul",
]
VALIDATION_COLUMNS = ["check_name", "status", "detail", "severity"]
