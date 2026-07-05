from __future__ import annotations

import pandas as pd

from models.plate_models import TRANSFER_COLUMNS, ensure_columns


def compile_hamilton_transfers(well_contents: pd.DataFrame, destination_labware: str = "PCR_plate_96") -> pd.DataFrame:
    rows = []
    ordered = well_contents.sort_values(["liquid_role", "source_labware", "source_well", "well"]).reset_index(drop=True)
    for idx, row in enumerate(ordered.itertuples(index=False), start=1):
        rows.append(
            {
                "step": idx,
                "source_labware": row.source_labware,
                "source_well": row.source_well,
                "destination_labware": destination_labware,
                "destination_well": row.well,
                "liquid_name": row.liquid_name,
                "volume_ul": float(row.volume_ul),
            }
        )
    return ensure_columns(pd.DataFrame(rows), TRANSFER_COLUMNS)
