"""Export generated design artifacts."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def build_all_results_xlsx(
    design: dict[str, Any],
    plate_map: pd.DataFrame,
    hamilton_worklist: pd.DataFrame,
    validation_report: pd.DataFrame,
) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{"DesignJSON": json.dumps(design, ensure_ascii=False, indent=2)}]).to_excel(writer, sheet_name="design_json", index=False)
        plate_map.to_excel(writer, sheet_name="plate_map", index=False)
        hamilton_worklist.to_excel(writer, sheet_name="hamilton_worklist", index=False)
        validation_report.to_excel(writer, sheet_name="validation_report", index=False)
    return output.getvalue()


def save_results(
    output_dir: str | Path,
    design: dict[str, Any],
    plate_map: pd.DataFrame,
    hamilton_worklist: pd.DataFrame,
    validation_report: pd.DataFrame,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    paths = {
        "plate_map": output_path / "plate_map.csv",
        "hamilton_worklist": output_path / "hamilton_worklist.csv",
        "validation_report": output_path / "validation_report.csv",
        "all_results": output_path / "all_results.xlsx",
        "design_json": output_path / "experiment_design.json",
    }
    plate_map.to_csv(paths["plate_map"], index=False)
    hamilton_worklist.to_csv(paths["hamilton_worklist"], index=False)
    validation_report.to_csv(paths["validation_report"], index=False)
    paths["all_results"].write_bytes(build_all_results_xlsx(design, plate_map, hamilton_worklist, validation_report))
    paths["design_json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths

