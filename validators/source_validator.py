from __future__ import annotations

import pandas as pd

from models.plate_models import ValidationResult


def validate_sources(well_contents: pd.DataFrame, source_map: pd.DataFrame) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    missing_source = well_contents[
        well_contents.get("source_labware", "").astype(str).str.strip().eq("")
        | well_contents.get("source_well", "").astype(str).str.strip().eq("")
    ]
    insufficient = source_map[source_map.get("status", "").astype(str) == "INSUFFICIENT"]
    unknown = source_map[source_map.get("status", "").astype(str) == "UNKNOWN"]
    ntc_template = well_contents[
        well_contents.get("label", "").astype(str).str.contains("NTC", case=False, na=False)
        & well_contents.get("liquid_role", "").astype(str).str.lower().eq("template")
    ]

    results.append(
        ValidationResult(
            "all_liquids_have_source_locations",
            "PASS" if missing_source.empty else "FAIL",
            "All liquids have source labware and source wells." if missing_source.empty else f"{len(missing_source)} rows are missing source locations.",
            "error" if not missing_source.empty else "info",
        )
    )
    results.append(
        ValidationResult(
            "source_volume_sufficiency",
            "PASS" if insufficient.empty else "FAIL",
            ("All required source volumes are available." if unknown.empty else f"{len(unknown)} source volumes are unknown.")
            if insufficient.empty
            else f"Insufficient sources: {', '.join(insufficient['liquid_name'].astype(str).tolist())}",
            "error" if not insufficient.empty else ("warning" if not unknown.empty else "info"),
        )
    )
    results.append(
        ValidationResult(
            "ntc_wells_do_not_contain_template",
            "PASS" if ntc_template.empty else "FAIL",
            "NTC wells do not contain template liquid." if ntc_template.empty else f"{len(ntc_template)} NTC content rows contain template.",
            "error" if not ntc_template.empty else "info",
        )
    )
    return results


def validation_results_to_df(results: list[ValidationResult]) -> pd.DataFrame:
    return pd.DataFrame([result.__dict__ for result in results])
