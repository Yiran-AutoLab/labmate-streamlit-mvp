from __future__ import annotations

import pandas as pd

from models.plate_models import TRANSFER_COLUMNS
from validators.layout_validator import validate_layout
from validators.source_validator import validate_sources, validation_results_to_df
from validators.volume_validator import validate_volumes
from workflows.transfer_compiler import compile_hamilton_transfers


def validation_passed(report: pd.DataFrame) -> bool:
    if report.empty:
        return False
    blocking = report[report["severity"].isin(["error", "critical"]) & (report["status"] != "PASS")]
    return blocking.empty


def run_validation(
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    spec: dict | None,
) -> dict[str, pd.DataFrame | bool]:
    spec = spec or {}
    expected_total = spec.get("expected_total_volume_ul")
    results = []
    results.extend(validate_layout(plate_layout))
    results.extend(validate_volumes(well_contents, expected_total))
    results.extend(validate_sources(well_contents, source_map))
    report = validation_results_to_df(results)
    passed = validation_passed(report)
    transfer_table = (
        compile_hamilton_transfers(well_contents, spec.get("destination_labware", "Plate_96"))
        if passed
        else pd.DataFrame(columns=TRANSFER_COLUMNS)
    )
    return {
        "validation_report": report,
        "transfer_table": transfer_table,
        "passed": passed,
    }
