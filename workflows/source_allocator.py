from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from models.plate_models import VALID_WELLS, WELL_CONTENT_COLUMNS, ensure_columns, is_valid_well, normalize_well


def assign_invalid_source_wells(
    well_contents: pd.DataFrame,
    sources: dict[str, dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Assign valid 96-well positions when an LLM returns names as source wells."""

    contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    normalized_sources = {key: dict(source) for key, source in sources.items()}
    used_by_labware: dict[str, set[str]] = defaultdict(set)
    location_by_liquid: dict[tuple[str, str], str] = {}

    # A valid location already present in well contents is authoritative.
    for row in contents.itertuples(index=False):
        labware = str(row.source_labware).strip()
        source_well = normalize_well(row.source_well)
        if labware and is_valid_well(source_well):
            used_by_labware[labware].add(source_well)
            location_by_liquid.setdefault(_liquid_key(labware, row.liquid_name, row.liquid_role), source_well)

    for source in normalized_sources.values():
        labware = str(source.get("source_labware", "")).strip()
        source_well = normalize_well(source.get("source_well", ""))
        if labware and is_valid_well(source_well):
            used_by_labware[labware].add(source_well)
            location_by_liquid.setdefault(
                _liquid_key(labware, source.get("liquid_name"), source.get("liquid_role")),
                source_well,
            )

    for source in normalized_sources.values():
        labware = str(source.get("source_labware", "")).strip()
        if not labware:
            continue
        key = _liquid_key(labware, source.get("liquid_name"), source.get("liquid_role"))
        source_well = normalize_well(source.get("source_well", ""))
        if not is_valid_well(source_well):
            source_well = location_by_liquid.get(key) or _next_available_well(used_by_labware[labware])
            location_by_liquid[key] = source_well
            used_by_labware[labware].add(source_well)
        source["source_well"] = source_well

    for index, row in contents.iterrows():
        labware = str(row["source_labware"]).strip()
        if not labware:
            continue
        key = _liquid_key(labware, row["liquid_name"], row["liquid_role"])
        source_well = normalize_well(row["source_well"])
        if not is_valid_well(source_well):
            source_well = location_by_liquid.get(key) or _next_available_well(used_by_labware[labware])
            location_by_liquid[key] = source_well
            used_by_labware[labware].add(source_well)
        contents.at[index, "source_well"] = source_well

    return contents, normalized_sources


def _liquid_key(labware: str, liquid_name: Any, liquid_role: Any) -> tuple[str, str]:
    identity = str(liquid_name).strip() or str(liquid_role).strip()
    return labware.casefold(), identity.casefold()


def _next_available_well(used: set[str]) -> str:
    for well in VALID_WELLS:
        if well not in used:
            return well
    raise ValueError("Not enough wells to place all liquids on the source plate.")
