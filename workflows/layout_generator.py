from __future__ import annotations

import pandas as pd

from models.plate_models import PLATE_LAYOUT_COLUMNS, VALID_WELLS, ensure_columns


def build_plate_matrix(plate_layout: pd.DataFrame) -> pd.DataFrame:
    lookup = {str(row.well).upper(): row.label for row in plate_layout.itertuples(index=False)}
    matrix = []
    for row in "ABCDEFGH":
        matrix.append([lookup.get(f"{row}{column}", "") for column in range(1, 13)])
    return pd.DataFrame(matrix, index=list("ABCDEFGH"), columns=[str(column) for column in range(1, 13)])


def reorder_layout(plate_layout: pd.DataFrame, mode: str = "row") -> pd.DataFrame:
    result = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS).copy().reset_index(drop=True)
    wells = VALID_WELLS if mode == "row" else [f"{row}{col}" for col in range(1, 13) for row in "ABCDEFGH"]
    result["well"] = wells[: len(result)]
    return result


def summary_matrix(plate_layout: pd.DataFrame) -> pd.DataFrame:
    if plate_layout.empty:
        return pd.DataFrame()
    return (
        plate_layout.groupby(["sample", "condition"], dropna=False)
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
