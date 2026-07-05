from __future__ import annotations

import pandas as pd

from models.plate_models import ValidationResult, is_valid_well


def validate_layout(plate_layout: pd.DataFrame) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    wells = plate_layout.get("well", pd.Series(dtype=str)).astype(str).str.upper()
    invalid = sorted(set(well for well in wells if not is_valid_well(well)))
    duplicates = sorted(set(wells[wells.duplicated()].tolist()))
    missing_labels = plate_layout[plate_layout.get("label", "").astype(str).str.strip() == ""] if "label" in plate_layout else plate_layout

    results.append(
        ValidationResult(
            "valid_96_well_addresses",
            "PASS" if not invalid else "FAIL",
            "All destination wells are valid." if not invalid else f"Invalid wells: {', '.join(invalid)}",
            "error" if invalid else "info",
        )
    )
    results.append(
        ValidationResult(
            "no_duplicated_destination_wells",
            "PASS" if not duplicates else "FAIL",
            "No duplicated destination wells." if not duplicates else f"Duplicated wells: {', '.join(duplicates)}",
            "error" if duplicates else "info",
        )
    )
    results.append(
        ValidationResult(
            "no_missing_labels",
            "PASS" if missing_labels.empty else "FAIL",
            "All layout rows have labels." if missing_labels.empty else f"{len(missing_labels)} layout rows are missing labels.",
            "error" if not missing_labels.empty else "info",
        )
    )
    return results
