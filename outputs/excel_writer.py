from __future__ import annotations

import io

import pandas as pd


def build_excel_workbook(
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    transfer_table: pd.DataFrame,
    validation_report: pd.DataFrame,
    summary_matrix: pd.DataFrame,
) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        plate_layout.to_excel(writer, sheet_name="Plate Layout", index=False)
        well_contents.to_excel(writer, sheet_name="Well Contents", index=False)
        source_map.to_excel(writer, sheet_name="Source Map", index=False)
        summary_matrix.to_excel(writer, sheet_name="Summary Matrix", index=False)
        transfer_table.to_excel(writer, sheet_name="Hamilton Transfer Table", index=False)
        validation_report.to_excel(writer, sheet_name="Validation Report", index=False)
    return buffer.getvalue()
