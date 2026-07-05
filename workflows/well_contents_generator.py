from __future__ import annotations

from typing import Any

import pandas as pd

from models.plate_models import SOURCE_MAP_COLUMNS, WELL_CONTENT_COLUMNS, ensure_columns


def generate_well_contents(plate_layout: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    if "well_contents" in spec and not spec["well_contents"].empty:
        return ensure_columns(spec["well_contents"], WELL_CONTENT_COLUMNS)

    rows: list[dict[str, Any]] = []
    volumes = spec["volumes"]
    sources = spec["sources"]
    for row in ensure_columns(plate_layout, ["well", "label", "sample", "condition", "replicate", "assay_type"]).itertuples(index=False):
        primer = str(row.condition)
        sample = str(row.sample)
        components = [
            ("Enzyme", "enzyme", volumes["enzyme"], sources["enzyme"]),
            ("Water", "water", volumes["water"], sources["water"]),
            (f"{primer}_Forward", "forward_primer", volumes["forward_primer"], sources[f"{primer}_forward"]),
            (f"{primer}_Reverse", "reverse_primer", volumes["reverse_primer"], sources[f"{primer}_reverse"]),
        ]
        if sample.upper() != "NTC":
            components.append((sample, "template", volumes["template"], sources[sample]))
        for liquid_name, liquid_role, volume, source in components:
            rows.append(
                {
                    "well": row.well,
                    "label": row.label,
                    "liquid_name": liquid_name,
                    "liquid_role": liquid_role,
                    "volume_ul": float(volume),
                    "source_labware": source["source_labware"],
                    "source_well": source["source_well"],
                }
            )
    return ensure_columns(pd.DataFrame(rows), WELL_CONTENT_COLUMNS)


def generate_source_map(well_contents: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    if well_contents.empty:
        return pd.DataFrame(columns=SOURCE_MAP_COLUMNS)
    required = (
        well_contents.groupby(["liquid_name", "liquid_role", "source_labware", "source_well"], dropna=False)["volume_ul"]
        .sum()
        .reset_index()
        .rename(columns={"volume_ul": "required_volume_ul"})
    )
    availability_lookup = {}
    for source in spec.get("sources", {}).values():
        key = (source["liquid_name"], source["liquid_role"], source["source_labware"], source["source_well"])
        availability_lookup[key] = float(source.get("available_volume_ul", 0))

    rows = []
    for row in required.itertuples(index=False):
        key = (row.liquid_name, row.liquid_role, row.source_labware, row.source_well)
        available = availability_lookup.get(key)
        if available is None or available <= 0:
            available = 0.0
            status = "UNKNOWN"
        else:
            status = "OK" if available >= float(row.required_volume_ul) else "INSUFFICIENT"
        rows.append(
            {
                "liquid_name": row.liquid_name,
                "liquid_role": row.liquid_role,
                "source_labware": row.source_labware,
                "source_well": row.source_well,
                "available_volume_ul": available,
                "required_volume_ul": float(row.required_volume_ul),
                "status": status,
            }
        )
    return ensure_columns(pd.DataFrame(rows), SOURCE_MAP_COLUMNS)
