from __future__ import annotations

import pandas as pd

from models.plate_models import ValidationResult


def validate_volumes(well_contents: pd.DataFrame, expected_total_volume_ul: float | None, min_pipette_ul: float = 0.5, max_pipette_ul: float = 300.0) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    volumes = pd.to_numeric(well_contents.get("volume_ul", pd.Series(dtype=float)), errors="coerce")
    bad = well_contents[volumes.isna() | (volumes < 0)]
    out_of_range = well_contents[(volumes < min_pipette_ul) | (volumes > max_pipette_ul)]
    totals = well_contents.assign(volume_ul=volumes).groupby("well", dropna=False)["volume_ul"].sum()
    bad_totals = pd.Series(dtype=float)
    if expected_total_volume_ul is not None:
        bad_totals = totals[(totals - expected_total_volume_ul).abs() > 1e-6]

    results.append(
        ValidationResult(
            "no_negative_or_missing_volumes",
            "PASS" if bad.empty else "FAIL",
            "All volumes are present and non-negative." if bad.empty else f"{len(bad)} rows have missing or negative volumes.",
            "error" if not bad.empty else "info",
        )
    )
    results.append(
        ValidationResult(
            "total_volume_per_well",
            "SKIP" if expected_total_volume_ul is None else ("PASS" if bad_totals.empty else "FAIL"),
            "No expected total volume was specified." if expected_total_volume_ul is None else (f"All wells total {expected_total_volume_ul:g} uL." if bad_totals.empty else f"Wells with wrong totals: {', '.join(bad_totals.index.astype(str).tolist())}"),
            "warning" if expected_total_volume_ul is None else ("error" if not bad_totals.empty else "info"),
        )
    )
    results.append(
        ValidationResult(
            "pipetting_volume_range",
            "PASS" if out_of_range.empty else "FAIL",
            f"All transfers are between {min_pipette_ul:g} and {max_pipette_ul:g} uL." if out_of_range.empty else f"{len(out_of_range)} transfers are outside pipetting range.",
            "error" if not out_of_range.empty else "info",
        )
    )
    return results
