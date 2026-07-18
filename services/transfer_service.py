from __future__ import annotations

import pandas as pd

from models.plate_models import PLATE_LAYOUT_COLUMNS, WELL_CONTENT_COLUMNS, ensure_columns
from workflows.layout_generator import summary_matrix
from workflows.transfer_compiler import compile_hamilton_transfers
from workflows.well_contents_generator import generate_source_map


def regenerate_derived_tables(
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    spec: dict | None,
) -> dict[str, pd.DataFrame]:
    if not spec:
        return {
            "plate_layout": ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS),
            "well_contents": ensure_columns(well_contents, WELL_CONTENT_COLUMNS),
            "source_map": pd.DataFrame(),
            "summary_matrix": pd.DataFrame(),
        }

    normalized_layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS)
    normalized_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    return {
        "plate_layout": normalized_layout,
        "well_contents": normalized_contents,
        "source_map": generate_source_map(normalized_contents, spec),
        "summary_matrix": summary_matrix(normalized_layout),
    }


def transfer_preview_table(well_contents: pd.DataFrame, spec: dict | None) -> pd.DataFrame:
    destination_labware = (spec or {}).get("destination_labware", "Plate_96")
    return compile_hamilton_transfers(well_contents, destination_labware)


def filtered_transfer_preview(
    well_contents: pd.DataFrame,
    spec: dict | None,
    liquids: list[str],
    wells: list[str],
) -> pd.DataFrame:
    preview = transfer_preview_table(well_contents, spec)
    if liquids:
        preview = preview[preview["liquid_name"].astype(str).isin(liquids)]
    if wells:
        preview = preview[preview["destination_well"].astype(str).isin(wells)]
    return preview.reset_index(drop=True)


def source_volume_summary(source_map: pd.DataFrame) -> pd.DataFrame:
    if source_map.empty:
        return pd.DataFrame(columns=["source_labware", "available_volume_ul", "required_volume_ul", "status"])
    summary = (
        source_map.assign(
            available_volume_ul=pd.to_numeric(source_map["available_volume_ul"], errors="coerce").fillna(0),
            required_volume_ul=pd.to_numeric(source_map["required_volume_ul"], errors="coerce").fillna(0),
        )
        .groupby("source_labware", dropna=False)[["available_volume_ul", "required_volume_ul"]]
        .sum()
        .reset_index()
    )
    summary["status"] = summary.apply(
        lambda row: "UNKNOWN" if row.available_volume_ul <= 0 else ("OK" if row.available_volume_ul >= row.required_volume_ul else "INSUFFICIENT"),
        axis=1,
    )
    return summary
