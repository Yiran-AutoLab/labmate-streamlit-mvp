"""Generate Hamilton-compatible worklist rows."""

from __future__ import annotations

import pandas as pd


WORKLIST_COLUMNS = [
    "Step",
    "Action",
    "SourceLabware",
    "SourcePosition",
    "DestLabware",
    "DestPosition",
    "Volume_uL",
    "LiquidClass",
    "TipStrategy",
    "MixAfter",
    "Notes",
]


def build_hamilton_worklist(design: dict, plate_map: pd.DataFrame) -> pd.DataFrame:
    rows = []
    step = 1
    tip_strategy = design.get("worklist", {}).get("tip_strategy", "new_tip_each_transfer")
    mix_after = design.get("worklist", {}).get("mix_after", "No")

    for plate_row in plate_map.itertuples(index=False):
        rows.append(
            {
                "Step": step,
                "Action": "AspirateDispense",
                "SourceLabware": design["medium"]["source_labware"],
                "SourcePosition": design["medium"]["source_position"],
                "DestLabware": plate_row.DestLabware,
                "DestPosition": plate_row.Well,
                "Volume_uL": plate_row.MediumVolume_uL,
                "LiquidClass": design["medium"].get("liquid_class", "Water"),
                "TipStrategy": tip_strategy,
                "MixAfter": "No",
                "Notes": f"Add medium to {plate_row.Group}",
            }
        )
        step += 1

    for plate_row in plate_map.itertuples(index=False):
        if bool(plate_row.IsBlank) or float(plate_row.SampleVolume_uL) <= 0:
            continue
        components = _split_semicolon(getattr(plate_row, "SampleComponents", ""))
        source_labwares = _split_semicolon(plate_row.SampleSourceLabware)
        source_positions = _split_semicolon(plate_row.SampleSourcePosition)
        transfer_count = max(len(source_positions), 1)
        component_volume = float(plate_row.SampleVolume_uL) / transfer_count

        for idx in range(transfer_count):
            component = components[idx] if idx < len(components) else plate_row.Group
            rows.append(
                {
                    "Step": step,
                    "Action": "AspirateDispense",
                    "SourceLabware": source_labwares[idx] if idx < len(source_labwares) else plate_row.SampleSourceLabware,
                    "SourcePosition": source_positions[idx] if idx < len(source_positions) else plate_row.SampleSourcePosition,
                    "DestLabware": plate_row.DestLabware,
                    "DestPosition": plate_row.Well,
                    "Volume_uL": component_volume,
                    "LiquidClass": design["sample"].get("liquid_class", "Water"),
                    "TipStrategy": tip_strategy,
                    "MixAfter": mix_after if idx == transfer_count - 1 else "No",
                    "Notes": f"Add {component} sample for {plate_row.Group} replicate {plate_row.Replicate}",
                }
            )
            step += 1

    return pd.DataFrame(rows, columns=WORKLIST_COLUMNS)


def _split_semicolon(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]
