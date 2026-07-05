"""Build 96-well OD600 plate maps."""

from __future__ import annotations

import pandas as pd


ROWS = list("ABCDEFGH")
COLUMNS = list(range(1, 13))
ALL_WELLS = [f"{row}{col}" for row in ROWS for col in COLUMNS]


def build_plate_map(design: dict) -> pd.DataFrame:
    blank_wells = list(dict.fromkeys(design.get("blank_wells", [])))
    occupied = set(blank_wells)
    available_wells = [well for well in ALL_WELLS if well not in occupied]

    records = []
    for well in blank_wells:
        records.append(_record_for_blank(well, design))

    well_index = 0
    for group in design.get("groups", []):
        for replicate in range(1, int(group.get("replicates", 0)) + 1):
            if well_index >= len(available_wells):
                raise ValueError("Not enough free wells in a 96-well plate for the requested design.")
            dest_well = available_wells[well_index]
            well_index += 1
            records.append(_record_for_sample(dest_well, group, replicate, design))

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=plate_map_columns())
    return df.sort_values("WellSort").drop(columns=["WellSort"]).reset_index(drop=True)


def plate_layout_matrix(plate_map: pd.DataFrame) -> pd.DataFrame:
    lookup = {row.Well: row.Group for row in plate_map.itertuples()}
    return pd.DataFrame(
        [[lookup.get(f"{row}{col}", "") for col in COLUMNS] for row in ROWS],
        index=ROWS,
        columns=[str(col) for col in COLUMNS],
    )


def plate_map_columns() -> list[str]:
    return [
        "Well",
        "Row",
        "Column",
        "Group",
        "Role",
        "Replicate",
        "IsBlank",
        "SampleComponents",
        "MediumVolume_uL",
        "SampleVolume_uL",
        "TotalVolume_uL",
        "SampleSourceLabware",
        "SampleSourcePosition",
        "DestLabware",
        "Assay",
    ]


def _record_for_blank(well: str, design: dict) -> dict:
    medium_volume = float(design["medium"]["volume_uL"])
    return {
        "Well": well,
        "Row": well[0],
        "Column": int(well[1:]),
        "Group": "Blank",
        "Role": "blank",
        "Replicate": "",
        "IsBlank": True,
        "SampleComponents": "",
        "MediumVolume_uL": medium_volume,
        "SampleVolume_uL": 0.0,
        "TotalVolume_uL": medium_volume,
        "SampleSourceLabware": "",
        "SampleSourcePosition": "",
        "DestLabware": design["dest_labware"],
        "Assay": "OD600",
        "WellSort": _well_sort_key(well),
    }


def _record_for_sample(well: str, group: dict, replicate: int, design: dict) -> dict:
    medium_volume = float(design["medium"]["volume_uL"])
    sample_volume = float(design["sample"]["volume_uL"])
    components = group.get("components") or [
        {
            "name": group["name"],
            "source_labware": group["source_labware"],
            "source_position": group["source_position"],
        }
    ]
    return {
        "Well": well,
        "Row": well[0],
        "Column": int(well[1:]),
        "Group": group["name"],
        "Role": group["role"],
        "Replicate": replicate,
        "IsBlank": False,
        "SampleComponents": ";".join(component["name"] for component in components),
        "MediumVolume_uL": medium_volume,
        "SampleVolume_uL": sample_volume,
        "TotalVolume_uL": medium_volume + sample_volume,
        "SampleSourceLabware": ";".join(component["source_labware"] for component in components),
        "SampleSourcePosition": ";".join(component["source_position"] for component in components),
        "DestLabware": design["dest_labware"],
        "Assay": "OD600",
        "WellSort": _well_sort_key(well),
    }


def _well_sort_key(well: str) -> int:
    return ROWS.index(well[0]) * 12 + int(well[1:]) - 1
