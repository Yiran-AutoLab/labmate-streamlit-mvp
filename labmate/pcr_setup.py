from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil

import pandas as pd


PLATE_ROWS = "ABCDEFGH"
PLATE_COLUMNS = 12
PLATE_WELL_COUNT = len(PLATE_ROWS) * PLATE_COLUMNS


@dataclass
class PCRSetupConfig:
    reaction_volume_uL: float = 20.0
    common_mix_volume_uL: float = 19.0
    template_volume_uL: float = 1.0
    number_of_samples: int = 24
    ntc_replicates: int = 2
    common_mix_source_labware: str = "Reservoir_1"
    common_mix_source_position: str = "A1"
    template_source_labware: str = "SampleRack_1"
    template_start_position: str = "B1"
    ntc_water_source_labware: str = "Reservoir_1"
    ntc_water_source_position: str = "A2"
    enzyme_volume_uL: float = 0.0
    water_volume_uL: float = 0.0
    forward_primer_volume_uL: float = 0.0
    reverse_primer_volume_uL: float = 0.0
    enzyme_source_labware: str = "Reservoir_1"
    enzyme_source_position: str = "A1"
    water_source_labware: str = "Reservoir_2"
    water_source_position: str = "A1"
    forward_primer_source_labware: str = "Reservoir_3"
    forward_primer_source_position: str = "A1"
    reverse_primer_source_labware: str = "Reservoir_4"
    reverse_primer_source_position: str = "A1"
    premix_labware: str = "PCR_premix_tube"
    premix_position: str = "A1"
    premix_first: bool = False
    destination_labware: str = "PCR_plate_96"
    destination_start_well: str = "A1"
    destination_orientation: str = "row_major"
    tip_strategy_common_mix: str = "reuse_tip_by_source"
    tip_strategy_template: str = "new_tip_each_transfer"
    mix_after: str = "Yes"
    overage_percent: float = 10.0


def _well_to_index(well: str) -> int:
    well = well.strip().upper()
    if len(well) < 2:
        raise ValueError(f"Invalid well position: {well}")
    row = well[0]
    if row not in PLATE_ROWS:
        raise ValueError(f"Invalid well row: {well}")
    try:
        column = int(well[1:])
    except ValueError as exc:
        raise ValueError(f"Invalid well column: {well}") from exc
    if column < 1 or column > PLATE_COLUMNS:
        raise ValueError(f"Invalid well column: {well}")
    return PLATE_ROWS.index(row) * PLATE_COLUMNS + (column - 1)


def _index_to_well(index: int) -> str:
    if index < 0 or index >= PLATE_WELL_COUNT:
        raise ValueError(f"Well index outside 96-well plate: {index}")
    row = PLATE_ROWS[index // PLATE_COLUMNS]
    column = (index % PLATE_COLUMNS) + 1
    return f"{row}{column}"


def _allocate_wells(start_well: str, count: int) -> list[str]:
    start_index = _well_to_index(start_well)
    return [_index_to_well(start_index + offset) for offset in range(count)]


def _allocate_wells_by_orientation(start_well: str, count: int, orientation: str) -> list[str]:
    if orientation != "column_major":
        return _allocate_wells(start_well, count)

    start_index = _well_to_index(start_well)
    start_row = start_index // PLATE_COLUMNS
    start_column = start_index % PLATE_COLUMNS
    wells = []
    for offset in range(count):
        position = start_row + offset
        row = position % len(PLATE_ROWS)
        column = start_column + position // len(PLATE_ROWS)
        if column >= PLATE_COLUMNS:
            raise ValueError(f"Well index outside 96-well plate from start well: {start_well}")
        wells.append(f"{PLATE_ROWS[row]}{column + 1}")
    return wells


def _destination_well_for_reaction(
    start_well: str,
    template_idx: int,
    pair_idx: int,
    template_count: int,
    layout_mode: str,
) -> str:
    start_index = _well_to_index(start_well)
    start_row = start_index // PLATE_COLUMNS
    start_column = start_index % PLATE_COLUMNS

    if layout_mode in {"horizontal_by_primer", "row_by_primer"}:
        columns_per_pair = min(template_count, PLATE_COLUMNS)
        rows_per_pair = ceil(template_count / PLATE_COLUMNS)
        local_row = template_idx // PLATE_COLUMNS
        local_column = template_idx % PLATE_COLUMNS
        row = start_row + pair_idx * rows_per_pair + local_row
        column = start_column + local_column
    else:
        rows_per_column = len(PLATE_ROWS)
        columns_per_pair = ceil(template_count / rows_per_column)
        local_column = template_idx // rows_per_column
        local_row = template_idx % rows_per_column
        row = start_row + local_row
        column = start_column + pair_idx * columns_per_pair + local_column

    if row >= len(PLATE_ROWS) or column >= PLATE_COLUMNS:
        raise ValueError(
            "PCR layout does not fit in one 96-well plate. "
            f"Template count={template_count}, primer pairs={pair_idx + 1}, layout={layout_mode}."
        )
    return f"{PLATE_ROWS[row]}{column + 1}"


def _pcr_layout_mode(config: PCRSetupConfig, template_count: int, primer_pair_count: int) -> str:
    requested = getattr(config, "destination_layout", "auto")
    if requested in {"horizontal_by_primer", "vertical_by_primer", "row_by_primer", "column_by_primer"}:
        return requested
    if template_count <= PLATE_COLUMNS and primer_pair_count <= len(PLATE_ROWS):
        return "horizontal_by_primer"
    return "vertical_by_primer"


def build_pcr_json(config: PCRSetupConfig) -> dict:
    return {
        "experiment_type": "PCR_SETUP",
        "reaction": asdict(config),
        "total_reactions": config.number_of_samples + config.ntc_replicates,
        "common_mix_calculation": calculate_common_mix_volume(config).to_dict(orient="records"),
        "common_mix_components": calculate_common_mix_components(config).to_dict(orient="records"),
    }


def generate_pcr_plate_map(config: PCRSetupConfig) -> pd.DataFrame:
    template_count = getattr(config, "template_count", config.number_of_samples)
    primer_pair_count = getattr(config, "primer_pair_count", 1)
    sample_reactions = template_count * primer_pair_count
    layout_mode = _pcr_layout_mode(config, template_count, primer_pair_count)
    template_positions = _allocate_wells(config.template_start_position, template_count)

    rows: list[dict] = []
    for pair_idx in range(primer_pair_count):
        for template_idx in range(template_count):
            rows.append(
                {
                    "Well": _destination_well_for_reaction(
                        config.destination_start_well,
                        template_idx,
                        pair_idx,
                        template_count,
                        layout_mode,
                    ),
                    "ReactionType": "Sample",
                    "PrimerPairID": f"PrimerPair_{pair_idx + 1}",
                    "TemplateID": f"Template_{template_idx + 1}",
                    "SampleID": f"P{pair_idx + 1}_Template_{template_idx + 1}",
                    "CommonMixVolume_uL": config.common_mix_volume_uL,
                    "TemplateOrWaterVolume_uL": config.template_volume_uL,
                    "TemplateSourceLabware": config.template_source_labware,
                    "TemplateSourcePosition": template_positions[template_idx],
                    "WaterSourceLabware": "",
                    "WaterSourcePosition": "",
                    "DestinationLabware": config.destination_labware,
                }
            )

    used_wells = {row["Well"] for row in rows}
    next_ntc_index = _well_to_index(config.destination_start_well)
    for idx in range(config.ntc_replicates):
        while next_ntc_index < PLATE_WELL_COUNT and _index_to_well(next_ntc_index) in used_wells:
            next_ntc_index += 1
        if next_ntc_index >= PLATE_WELL_COUNT:
            raise ValueError("No free destination well is available for NTC reactions.")
        ntc_well = _index_to_well(next_ntc_index)
        used_wells.add(ntc_well)
        rows.append(
            {
                "Well": ntc_well,
                "ReactionType": "NTC",
                "PrimerPairID": "",
                "TemplateID": "",
                "SampleID": f"NTC_{idx + 1}",
                "CommonMixVolume_uL": config.common_mix_volume_uL,
                "TemplateOrWaterVolume_uL": config.template_volume_uL,
                "TemplateSourceLabware": "",
                "TemplateSourcePosition": "",
                "WaterSourceLabware": config.ntc_water_source_labware,
                "WaterSourcePosition": config.ntc_water_source_position,
                "DestinationLabware": config.destination_labware,
            }
        )

    return pd.DataFrame(rows)


def generate_pcr_worklist(config: PCRSetupConfig) -> pd.DataFrame:
    plate_map = generate_pcr_plate_map(config)
    rows: list[dict] = []
    step = 1
    primer_pairs = getattr(config, "primer_pairs", None)
    primer_pair_count = getattr(config, "primer_pair_count", 1)
    template_count = getattr(config, "template_count", config.number_of_samples)
    has_component_mix = (
        config.enzyme_volume_uL
        + config.water_volume_uL
        + config.forward_primer_volume_uL
        + config.reverse_primer_volume_uL
    ) > 0
    should_premix_first = config.premix_first or (primer_pair_count > 1 and has_component_mix)

    if should_premix_first and primer_pair_count > 1 and primer_pairs:
        multiplier = 1 + config.overage_percent / 100
        for pair_idx, pair in enumerate(primer_pairs):
            premix_position = _index_to_well(pair_idx)
            premix_components = [
                ("Enzyme", config.enzyme_volume_uL, config.enzyme_source_labware, config.enzyme_source_position),
                ("Water", config.water_volume_uL, config.water_source_labware, config.water_source_position),
                (
                    "ForwardPrimer",
                    config.forward_primer_volume_uL,
                    pair["forward_source_labware"],
                    pair["forward_source_position"],
                ),
                (
                    "ReversePrimer",
                    config.reverse_primer_volume_uL,
                    pair["reverse_source_labware"],
                    pair["reverse_source_position"],
                ),
            ]
            for component, volume, labware, position in premix_components:
                if volume <= 0:
                    continue
                rows.append(
                    {
                        "Step": step,
                        "Action": "PrepareCommonMix",
                        "SourceLabware": labware,
                        "SourcePosition": position,
                        "DestLabware": config.premix_labware,
                        "DestPosition": premix_position,
                        "Volume_uL": volume * template_count * multiplier,
                        "LiquidClass": component,
                        "TipStrategy": "new_tip_each_transfer",
                        "MixAfter": "No",
                        "Notes": f"PrimerPair_{pair_idx + 1}: {volume} uL x templates + {config.overage_percent}% overage",
                    }
                )
                step += 1
    elif should_premix_first:
        for component in _common_mix_components(config):
            rows.append(
                {
                    "Step": step,
                    "Action": "PrepareCommonMix",
                    "SourceLabware": component["SourceLabware"],
                    "SourcePosition": component["SourcePosition"],
                    "DestLabware": config.premix_labware,
                    "DestPosition": config.premix_position,
                    "Volume_uL": component["TotalVolumeWithOverage_uL"],
                    "LiquidClass": component["Component"],
                    "TipStrategy": "new_tip_each_transfer",
                    "MixAfter": "No",
                    "Notes": f"{component['PerReaction_uL']} uL x reactions + {config.overage_percent}% overage",
                }
            )
            step += 1

    for _, reaction in plate_map.iterrows():
        pair_id = reaction.get("PrimerPairID", "")
        source_position = config.premix_position
        if should_premix_first and primer_pair_count > 1 and pair_id:
            source_position = _index_to_well(int(str(pair_id).split("_")[-1]) - 1)
        rows.append(
            {
                "Step": step,
                "Action": "TransferCommonMix",
                "SourceLabware": config.premix_labware if should_premix_first else config.common_mix_source_labware,
                "SourcePosition": source_position if should_premix_first else config.common_mix_source_position,
                "DestLabware": config.destination_labware,
                "DestPosition": reaction["Well"],
                "Volume_uL": config.common_mix_volume_uL,
                "LiquidClass": "PCRCommonMix",
                "TipStrategy": config.tip_strategy_common_mix,
                "MixAfter": "No",
                "Notes": reaction["SampleID"],
            }
        )
        step += 1

    for _, reaction in plate_map.iterrows():
        is_ntc = reaction["ReactionType"] == "NTC"
        rows.append(
            {
                "Step": step,
                "Action": "TransferWater" if is_ntc else "TransferTemplate",
                "SourceLabware": reaction["WaterSourceLabware"] if is_ntc else reaction["TemplateSourceLabware"],
                "SourcePosition": reaction["WaterSourcePosition"] if is_ntc else reaction["TemplateSourcePosition"],
                "DestLabware": config.destination_labware,
                "DestPosition": reaction["Well"],
                "Volume_uL": config.template_volume_uL,
                "LiquidClass": "Water" if is_ntc else "Template",
                "TipStrategy": config.tip_strategy_template,
                "MixAfter": config.mix_after,
                "Notes": reaction["SampleID"],
            }
        )
        step += 1

    return pd.DataFrame(rows)


def calculate_common_mix_volume(config: PCRSetupConfig) -> pd.DataFrame:
    total_reactions = config.number_of_samples + config.ntc_replicates
    base_volume = total_reactions * config.common_mix_volume_uL
    overage_volume = base_volume * config.overage_percent / 100
    total_volume = base_volume + overage_volume
    return pd.DataFrame(
        [
            {
                "TotalReactions": total_reactions,
                "CommonMixPerReaction_uL": config.common_mix_volume_uL,
                "BaseCommonMixVolume_uL": base_volume,
                "OveragePercent": config.overage_percent,
                "OverageVolume_uL": overage_volume,
                "TotalCommonMixVolume_uL": total_volume,
            }
        ]
    )


def _common_mix_components(config: PCRSetupConfig) -> list[dict]:
    total_reactions = config.number_of_samples + config.ntc_replicates
    multiplier = 1 + config.overage_percent / 100
    components = [
        (
            "Enzyme",
            config.enzyme_volume_uL,
            config.enzyme_source_labware,
            config.enzyme_source_position,
        ),
        (
            "Water",
            config.water_volume_uL,
            config.water_source_labware,
            config.water_source_position,
        ),
        (
            "ForwardPrimer",
            config.forward_primer_volume_uL,
            config.forward_primer_source_labware,
            config.forward_primer_source_position,
        ),
        (
            "ReversePrimer",
            config.reverse_primer_volume_uL,
            config.reverse_primer_source_labware,
            config.reverse_primer_source_position,
        ),
    ]
    return [
        {
            "Component": component,
            "PerReaction_uL": volume,
            "BaseVolume_uL": volume * total_reactions,
            "OveragePercent": config.overage_percent,
            "TotalVolumeWithOverage_uL": volume * total_reactions * multiplier,
            "SourceLabware": labware,
            "SourcePosition": position,
            "PremixLabware": config.premix_labware,
            "PremixPosition": config.premix_position,
        }
        for component, volume, labware, position in components
        if volume > 0
    ]


def calculate_common_mix_components(config: PCRSetupConfig) -> pd.DataFrame:
    return pd.DataFrame(_common_mix_components(config))


def validate_pcr_setup(config: PCRSetupConfig) -> pd.DataFrame:
    rows: list[dict] = []

    def add(level: str, check: str, message: str) -> None:
        rows.append({"Level": level, "Check": check, "Message": message})

    total_reactions = config.number_of_samples + config.ntc_replicates
    expected_volume = config.common_mix_volume_uL + config.template_volume_uL
    if abs(expected_volume - config.reaction_volume_uL) > 1e-9:
        add(
            "ERROR",
            "reaction_volume",
            "common_mix_volume_uL + template_volume_uL must equal reaction_volume_uL.",
        )
    else:
        add("OK", "reaction_volume", "Reaction component volumes match total reaction volume.")

    component_volume = (
        config.enzyme_volume_uL
        + config.water_volume_uL
        + config.forward_primer_volume_uL
        + config.reverse_primer_volume_uL
    )
    if config.premix_first and abs(component_volume - config.common_mix_volume_uL) > 1e-9:
        add(
            "ERROR",
            "premix_components",
            "Enzyme + water + forward primer + reverse primer must equal common_mix_volume_uL.",
        )
    elif config.premix_first:
        add("OK", "premix_components", "Premix component volumes match common mix volume.")

    try:
        plate_map = generate_pcr_plate_map(config)
        duplicated_wells = plate_map[plate_map["Well"].duplicated()]["Well"].tolist()
        if duplicated_wells:
            add("ERROR", "destination_collisions", f"Destination wells collide: {', '.join(duplicated_wells)}")
        else:
            add("OK", "destination_collisions", "No destination well collisions.")

        ntc_rows = plate_map[plate_map["ReactionType"] == "NTC"]
        ntc_uses_water = (
            (ntc_rows["TemplateSourceLabware"] == "")
            & (ntc_rows["TemplateSourcePosition"] == "")
            & (ntc_rows["WaterSourceLabware"] == config.ntc_water_source_labware)
            & (ntc_rows["WaterSourcePosition"] == config.ntc_water_source_position)
        ).all()
        if ntc_uses_water:
            add("OK", "ntc_water", "NTC wells receive water instead of template.")
        else:
            add("ERROR", "ntc_water", "NTC wells must receive water instead of template.")
    except ValueError as exc:
        add("ERROR", "well_allocation", str(exc))

    if config.template_volume_uL < 1:
        add("WARNING", "template_volume", "Template transfer volume is below 1 uL.")
    else:
        add("OK", "template_volume", "Template transfer volume is at least 1 uL.")

    if total_reactions > PLATE_WELL_COUNT:
        add("WARNING", "plate_capacity", "Total reaction count exceeds 96.")
    else:
        add("OK", "plate_capacity", "Total reaction count fits within a 96-well plate.")

    total_common_mix = calculate_common_mix_volume(config).iloc[0]["TotalCommonMixVolume_uL"]
    add(
        "OK",
        "common_mix_total",
        f"Common mix total volume with overage: {total_common_mix:.2f} uL.",
    )

    return pd.DataFrame(rows)


def build_generic_pcr_design_from_slots(slots: dict) -> dict:
    template_count = int(float(slots.get("template_count", slots.get("number_of_templates", 24)) or 24))
    primer_pair_count = int(float(slots.get("primer_pair_count", 1) or 1))
    template_volume = float(slots.get("template_volume_uL", 1.0) or 1.0)
    enzyme_volume = float(slots.get("enzyme_volume_uL", 0.0) or 0.0)
    water_volume = float(slots.get("water_volume_uL", 0.0) or 0.0)
    forward_volume = float(slots.get("forward_primer_volume_uL", slots.get("primer_each_volume_uL", 0.0)) or 0.0)
    reverse_volume = float(slots.get("reverse_primer_volume_uL", slots.get("primer_each_volume_uL", 0.0)) or 0.0)
    reaction_volume = float(
        slots.get(
            "reaction_volume_uL",
            enzyme_volume + water_volume + forward_volume + reverse_volume + template_volume,
        )
        or 0.0
    )
    destination_layout = str(slots.get("destination_layout", "auto") or "auto")
    order = "primer_pair_major"
    if destination_layout in {"horizontal", "row", "horizontal_by_primer", "numbers_change_horizontal"}:
        order = "primer_pair_major_horizontal"
    elif destination_layout in {"vertical", "column", "vertical_by_primer", "letters_change_vertical"}:
        order = "primer_pair_major_vertical"

    reagents = [
        {
            "name": "Enzyme",
            "source_labware": str(slots.get("enzyme_source_labware", "Reservoir_1") or "Reservoir_1"),
            "source_position": str(slots.get("enzyme_source_position", "A1") or "A1"),
        },
        {
            "name": "Water",
            "source_labware": str(slots.get("water_source_labware", "Reservoir_1") or "Reservoir_1"),
            "source_position": str(slots.get("water_source_position", "A2") or "A2"),
        },
        {
            "name": "ForwardPrimer",
            "source_labware": str(slots.get("forward_primer_source_labware", "Reservoir_1") or "Reservoir_1"),
            "source_position": str(slots.get("forward_primer_source_position", "A3") or "A3"),
        },
        {
            "name": "ReversePrimer",
            "source_labware": str(slots.get("reverse_primer_source_labware", "Reservoir_1") or "Reservoir_1"),
            "source_position": str(slots.get("reverse_primer_source_position", "A4") or "A4"),
        },
        {
            "name": "Template",
            "source_labware": str(slots.get("template_source_labware", "SampleRack_1") or "SampleRack_1"),
            "source_position": str(slots.get("template_start_position", "B1") or "B1"),
        },
        {
            "name": "NTCWater",
            "source_labware": str(slots.get("ntc_water_source_labware", slots.get("water_source_labware", "Reservoir_1")) or "Reservoir_1"),
            "source_position": str(slots.get("ntc_water_source_position", slots.get("water_source_position", "A2")) or "A2"),
        },
    ]
    reaction_recipe = [
        {"component": "Enzyme", "volume_uL": enzyme_volume},
        {"component": "Water", "volume_uL": water_volume},
        {"component": "ForwardPrimer", "volume_uL": forward_volume},
        {"component": "ReversePrimer", "volume_uL": reverse_volume},
        {"component": "Template", "volume_uL": template_volume},
    ]
    return {
        "task_type": "PCR_SETUP",
        "mode": "reaction_table",
        "reagents": reagents,
        "reaction_recipe": reaction_recipe,
        "reaction_design": {
            "templates": template_count,
            "primer_pairs": primer_pair_count,
            "replicates": int(float(slots.get("replicates", 1) or 1)),
            "ntc": int(float(slots.get("ntc_replicates", slots.get("ntc", 0)) or 0)),
            "layout": "all_templates_by_all_primer_pairs",
        },
        "plate_layout": {
            "plate_type": "96_well",
            "destination_labware": str(slots.get("destination_labware", "PCR_plate_96") or "PCR_plate_96"),
            "start_well": str(slots.get("destination_start_well", "A1") or "A1"),
            "order": order,
        },
        "operations": [
            {"op": "make_premix", "scope": "per_primer_pair", "batch_size": slots.get("premix_batch_size")},
            {"op": "dispense_premix_to_plate"},
            {"op": "add_template"},
            {"op": "validate"},
        ],
        "reaction_volume_uL": reaction_volume,
        "overage_percent": float(slots.get("overage_percent", 10.0) or 10.0),
        "premix_labware": str(slots.get("premix_labware", "PCR_premix_tube") or "PCR_premix_tube"),
        "premix_start_position": str(slots.get("premix_position", "A1") or "A1"),
        "missing_fields": [],
    }


def expand_pcr_design_to_reaction_table(pcr_design: dict) -> pd.DataFrame:
    recipe = _recipe_map(pcr_design)
    design = pcr_design.get("reaction_design", {})
    layout = pcr_design.get("plate_layout", {})
    template_count = int(float(design.get("templates", 1) or 1))
    primer_pair_count = int(float(design.get("primer_pairs", 1) or 1))
    replicates = int(float(design.get("replicates", 1) or 1))
    ntc_replicates = int(float(design.get("ntc", 0) or 0))
    start_well = str(layout.get("start_well", "A1") or "A1")
    destination_labware = str(layout.get("destination_labware", "PCR_plate_96") or "PCR_plate_96")
    order = str(layout.get("order", "primer_pair_major") or "primer_pair_major")
    layout_mode = _layout_mode_from_design(template_count, primer_pair_count, order)

    rows: list[dict] = []
    reaction_idx = 0
    for pair_idx in range(primer_pair_count):
        for template_idx in range(template_count):
            for replicate_idx in range(replicates):
                well = _destination_well_for_reaction(start_well, reaction_idx, 0, PLATE_WELL_COUNT, "horizontal_by_primer")
                if order.startswith("primer_pair_major"):
                    well = _destination_well_for_reaction(start_well, template_idx, pair_idx, template_count, layout_mode)
                rows.append(
                    _reaction_table_row(
                        well=well,
                        reaction_type="Sample",
                        template_id=f"Template_{template_idx + 1}",
                        primer_pair_id=f"PrimerPair_{pair_idx + 1}",
                        replicate=replicate_idx + 1,
                        recipe=recipe,
                        destination_labware=destination_labware,
                    )
                )
                reaction_idx += 1

    used_wells = {row["Well"] for row in rows}
    next_index = _well_to_index(start_well)
    for pair_idx in range(primer_pair_count):
        for ntc_idx in range(ntc_replicates):
            while next_index < PLATE_WELL_COUNT and _index_to_well(next_index) in used_wells:
                next_index += 1
            if next_index >= PLATE_WELL_COUNT:
                raise ValueError("PCR layout does not fit in one 96-well plate.")
            well = _index_to_well(next_index)
            used_wells.add(well)
            rows.append(
                _reaction_table_row(
                    well=well,
                    reaction_type="NTC",
                    template_id=f"NTC_{ntc_idx + 1}",
                    primer_pair_id=f"PrimerPair_{pair_idx + 1}",
                    replicate=ntc_idx + 1,
                    recipe=recipe,
                    destination_labware=destination_labware,
                )
            )

    return pd.DataFrame(rows)


def generate_pcr_plate_map_from_reaction_table(reaction_table: pd.DataFrame, pcr_design: dict) -> pd.DataFrame:
    columns = [
        "Well",
        "ReactionType",
        "TemplateID",
        "PrimerPairID",
        "Replicate",
        "TotalVolume_uL",
        "DestinationLabware",
    ]
    available = [column for column in columns if column in reaction_table.columns]
    plate_map = reaction_table[available].copy()
    if "DestinationWell" not in plate_map.columns:
        plate_map["DestinationWell"] = plate_map["Well"]
    return plate_map


def calculate_common_mix_from_reaction_table(reaction_table: pd.DataFrame, pcr_design: dict) -> pd.DataFrame:
    overage = float(pcr_design.get("overage_percent", 10.0) or 10.0)
    premix_volume = _component_volume(reaction_table, "Enzyme") + _component_volume(reaction_table, "Water")
    premix_volume += _component_volume(reaction_table, "ForwardPrimer") + _component_volume(reaction_table, "ReversePrimer")
    total_reactions = len(reaction_table)
    base_volume = total_reactions * premix_volume
    return pd.DataFrame(
        [
            {
                "TotalReactions": total_reactions,
                "CommonMixPerReaction_uL": premix_volume,
                "BaseCommonMixVolume_uL": base_volume,
                "OveragePercent": overage,
                "OverageVolume_uL": base_volume * overage / 100,
                "TotalCommonMixVolume_uL": base_volume * (1 + overage / 100),
            }
        ]
    )


def calculate_common_mix_components_from_reaction_table(reaction_table: pd.DataFrame, pcr_design: dict) -> pd.DataFrame:
    overage = float(pcr_design.get("overage_percent", 10.0) or 10.0)
    reagents = _reagent_map(pcr_design)
    primer_pairs = sorted(str(value) for value in reaction_table["PrimerPairID"].dropna().unique())
    rows: list[dict] = []
    for pair_id in primer_pairs:
        pair_table = reaction_table[reaction_table["PrimerPairID"].astype(str) == pair_id]
        pair_idx = int(pair_id.split("_")[-1]) - 1 if "_" in pair_id else 0
        premix_position = _premix_position_for_pair(pcr_design, pair_idx)
        for component in ["Enzyme", "Water", "ForwardPrimer", "ReversePrimer"]:
            per_reaction = _component_volume(pair_table, component)
            if per_reaction <= 0:
                continue
            reagent = reagents.get(component, {})
            rows.append(
                {
                    "PrimerPairID": pair_id,
                    "Component": component,
                    "PerReaction_uL": per_reaction,
                    "BaseVolume_uL": per_reaction * len(pair_table),
                    "OveragePercent": overage,
                    "TotalVolumeWithOverage_uL": per_reaction * len(pair_table) * (1 + overage / 100),
                    "SourceLabware": reagent.get("source_labware", ""),
                    "SourcePosition": _source_position_for_component(component, reagent.get("source_position", ""), pair_idx),
                    "PremixLabware": pcr_design.get("premix_labware", "PCR_premix_tube"),
                    "PremixPosition": premix_position,
                }
            )
    return pd.DataFrame(rows)


def generate_pcr_worklist_from_reaction_table(reaction_table: pd.DataFrame, pcr_design: dict) -> pd.DataFrame:
    reagents = _reagent_map(pcr_design)
    overage = float(pcr_design.get("overage_percent", 10.0) or 10.0)
    premix_labware = str(pcr_design.get("premix_labware", "PCR_premix_tube") or "PCR_premix_tube")
    rows: list[dict] = []
    step = 1

    primer_pairs = sorted(str(value) for value in reaction_table["PrimerPairID"].dropna().unique())
    for pair_id in primer_pairs:
        pair_idx = int(pair_id.split("_")[-1]) - 1 if "_" in pair_id else 0
        pair_table = reaction_table[reaction_table["PrimerPairID"].astype(str) == pair_id]
        premix_position = _premix_position_for_pair(pcr_design, pair_idx)
        for component in ["Enzyme", "Water", "ForwardPrimer", "ReversePrimer"]:
            volume = _component_volume(pair_table, component)
            if volume <= 0:
                continue
            reagent = reagents.get(component, {})
            rows.append(
                _worklist_row(
                    step,
                    "PrepareCommonMix",
                    reagent.get("source_labware", ""),
                    _source_position_for_component(component, reagent.get("source_position", ""), pair_idx),
                    premix_labware,
                    premix_position,
                    volume * len(pair_table) * (1 + overage / 100),
                    component,
                    f"{pair_id}: {volume} uL x {len(pair_table)} reactions + {overage}% overage",
                )
            )
            step += 1

    for _, reaction in reaction_table.iterrows():
        pair_id = str(reaction.get("PrimerPairID", "PrimerPair_1"))
        pair_idx = int(pair_id.split("_")[-1]) - 1 if "_" in pair_id else 0
        premix_volume = (
            _cell_float(reaction, "Enzyme_uL")
            + _cell_float(reaction, "Water_uL")
            + _cell_float(reaction, "ForwardPrimer_uL")
            + _cell_float(reaction, "ReversePrimer_uL")
        )
        rows.append(
            _worklist_row(
                step,
                "TransferCommonMix",
                premix_labware,
                _premix_position_for_pair(pcr_design, pair_idx),
                reaction.get("DestinationLabware", pcr_design.get("plate_layout", {}).get("destination_labware", "PCR_plate_96")),
                reaction.get("Well"),
                premix_volume,
                "PCRCommonMix",
                str(reaction.get("TemplateID", "")),
                tip_strategy="reuse_tip_by_source",
            )
        )
        step += 1

    template_reagent = reagents.get("Template", {})
    ntc_reagent = reagents.get("NTCWater", reagents.get("Water", {}))
    template_start = str(template_reagent.get("source_position", "B1") or "B1")
    for _, reaction in reaction_table.iterrows():
        is_ntc = str(reaction.get("ReactionType", "")).upper() == "NTC" or str(reaction.get("TemplateID", "")).upper().startswith("NTC")
        template_id = str(reaction.get("TemplateID", "Template_1"))
        template_idx = int(template_id.split("_")[-1]) - 1 if template_id.startswith("Template_") else 0
        source_labware = ntc_reagent.get("source_labware", "") if is_ntc else template_reagent.get("source_labware", "")
        source_position = ntc_reagent.get("source_position", "") if is_ntc else _template_source_position(template_start, template_idx)
        rows.append(
            _worklist_row(
                step,
                "TransferWater" if is_ntc else "TransferTemplate",
                source_labware,
                source_position,
                reaction.get("DestinationLabware", pcr_design.get("plate_layout", {}).get("destination_labware", "PCR_plate_96")),
                reaction.get("Well"),
                _cell_float(reaction, "Template_uL"),
                "Water" if is_ntc else "Template",
                template_id,
                tip_strategy="new_tip_each_transfer",
                mix_after="Yes",
            )
        )
        step += 1

    return pd.DataFrame(rows)


def validate_pcr_reaction_table(
    reaction_table: pd.DataFrame,
    pcr_design: dict | None = None,
    robot_min_volume_uL: float = 1.0,
) -> pd.DataFrame:
    pcr_design = pcr_design or {}
    rows: list[dict] = []

    def add(level: str, check: str, message: str) -> None:
        rows.append({"Level": level, "Check": check, "Message": message})

    if reaction_table.empty:
        add("ERROR", "reaction_table", "Reaction table is empty.")
        return pd.DataFrame(rows)

    duplicate_wells = reaction_table[reaction_table["Well"].duplicated()]["Well"].astype(str).tolist()
    if duplicate_wells:
        add("ERROR", "duplicate_wells", f"Duplicate wells: {', '.join(duplicate_wells)}")
    else:
        add("OK", "duplicate_wells", "No duplicate destination wells.")

    if len(reaction_table) > PLATE_WELL_COUNT:
        add("ERROR", "plate_capacity", "Number of reactions exceeds 96 wells.")
    else:
        add("OK", "plate_capacity", "Number of reactions fits in a 96-well plate.")

    expected_total = pcr_design.get("reaction_volume_uL")
    volume_columns = [column for column in reaction_table.columns if column.endswith("_uL") and column != "TotalVolume_uL"]
    calculated_total = reaction_table[volume_columns].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    if "TotalVolume_uL" in reaction_table.columns:
        stated_total = pd.to_numeric(reaction_table["TotalVolume_uL"], errors="coerce").fillna(0)
        mismatches = reaction_table.loc[(calculated_total - stated_total).abs() > 1e-9, "Well"].astype(str).tolist()
        if mismatches:
            add("ERROR", "total_volume", f"TotalVolume_uL does not match component sum in wells: {', '.join(mismatches)}")
        else:
            add("OK", "total_volume", "Total volume matches component sums.")
    if expected_total not in {None, ""}:
        expected_total = float(expected_total)
        mismatches = reaction_table.loc[(calculated_total - expected_total).abs() > 1e-9, "Well"].astype(str).tolist()
        if mismatches:
            add("WARNING", "expected_volume", f"Some wells do not equal expected reaction volume {expected_total} uL.")
        else:
            add("OK", "expected_volume", "All wells match expected reaction volume.")

    low_volume_messages = []
    for column in volume_columns:
        low_rows = reaction_table[(pd.to_numeric(reaction_table[column], errors="coerce").fillna(0) > 0) & (pd.to_numeric(reaction_table[column], errors="coerce").fillna(0) < robot_min_volume_uL)]
        if not low_rows.empty:
            low_volume_messages.append(f"{column}: {', '.join(low_rows['Well'].astype(str).tolist())}")
    if low_volume_messages:
        add("WARNING", "minimum_transfer_volume", "; ".join(low_volume_messages))
    else:
        add("OK", "minimum_transfer_volume", "All transfer volumes are at or above robot minimum.")

    reagents = _reagent_map(pcr_design)
    missing_sources = []
    for component in ["Enzyme", "Water", "ForwardPrimer", "ReversePrimer", "Template"]:
        if _component_volume(reaction_table, component) <= 0:
            continue
        reagent = reagents.get(component, {})
        if not reagent.get("source_labware") or not reagent.get("source_position"):
            missing_sources.append(component)
    if missing_sources:
        add("ERROR", "source_positions", f"Missing source positions for: {', '.join(missing_sources)}")
    else:
        add("OK", "source_positions", "All used components have source positions.")

    ntc_rows = reaction_table[
        (reaction_table.get("ReactionType", pd.Series(dtype=str)).astype(str).str.upper() == "NTC")
        | (reaction_table.get("TemplateID", pd.Series(dtype=str)).astype(str).str.upper().str.startswith("NTC"))
    ]
    if not ntc_rows.empty and any(~ntc_rows["TemplateID"].astype(str).str.upper().str.startswith("NTC")):
        add("ERROR", "ntc_template", "NTC wells must not receive a normal template ID.")
    else:
        add("OK", "ntc_template", "NTC rows are marked to receive water instead of template.")

    for operation in pcr_design.get("operations", []):
        if operation.get("op") == "make_premix":
            batch_size = operation.get("batch_size")
            if batch_size in {None, ""}:
                add("OK", "premix_batch_size", "Premix is treated as one sufficient batch per primer pair.")
            elif float(batch_size) < len(reaction_table):
                add("WARNING", "premix_batch_size", "Configured premix batch_size is smaller than reaction table size.")
            else:
                add("OK", "premix_batch_size", "Premix batch_size is sufficient.")
            break

    return pd.DataFrame(rows)


def _recipe_map(pcr_design: dict) -> dict[str, float]:
    return {
        str(item.get("component")): float(item.get("volume_uL", 0) or 0)
        for item in pcr_design.get("reaction_recipe", [])
        if item.get("component")
    }


def _reagent_map(pcr_design: dict) -> dict[str, dict]:
    return {
        str(item.get("name")): item
        for item in pcr_design.get("reagents", [])
        if item.get("name")
    }


def _layout_mode_from_design(template_count: int, primer_pair_count: int, order: str) -> str:
    if "horizontal" in order:
        return "horizontal_by_primer"
    if "vertical" in order:
        return "vertical_by_primer"
    if template_count <= PLATE_COLUMNS and primer_pair_count <= len(PLATE_ROWS):
        return "horizontal_by_primer"
    return "vertical_by_primer"


def _reaction_table_row(
    well: str,
    reaction_type: str,
    template_id: str,
    primer_pair_id: str,
    replicate: int,
    recipe: dict[str, float],
    destination_labware: str,
) -> dict:
    total = sum(recipe.get(component, 0.0) for component in ["Enzyme", "Water", "ForwardPrimer", "ReversePrimer", "Template"])
    return {
        "Well": well,
        "ReactionType": reaction_type,
        "TemplateID": template_id,
        "PrimerPairID": primer_pair_id,
        "Replicate": replicate,
        "Enzyme_uL": recipe.get("Enzyme", 0.0),
        "Water_uL": recipe.get("Water", 0.0),
        "ForwardPrimer_uL": recipe.get("ForwardPrimer", 0.0),
        "ReversePrimer_uL": recipe.get("ReversePrimer", 0.0),
        "Template_uL": recipe.get("Template", 0.0),
        "TotalVolume_uL": total,
        "DestinationLabware": destination_labware,
        "DestinationWell": well,
    }


def _component_volume(table: pd.DataFrame, component: str) -> float:
    column = f"{component}_uL"
    if column not in table.columns or table.empty:
        return 0.0
    return float(pd.to_numeric(table[column], errors="coerce").fillna(0).iloc[0])


def _cell_float(row: pd.Series, column: str) -> float:
    try:
        return float(row.get(column, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _source_position_for_component(component: str, start_position: str, pair_idx: int) -> str:
    if component in {"ForwardPrimer", "ReversePrimer"}:
        return _primer_pair_source_position(start_position, pair_idx)
    return start_position


def _primer_pair_source_position(start_well: str, pair_idx: int) -> str:
    start_well = str(start_well or "").strip().upper()
    try:
        start_index = _well_to_index(start_well)
    except ValueError:
        return start_well
    start_row = start_index // PLATE_COLUMNS
    start_column = start_index % PLATE_COLUMNS
    row_position = start_row + pair_idx
    row = row_position % len(PLATE_ROWS)
    column = start_column + row_position // len(PLATE_ROWS)
    if column >= PLATE_COLUMNS:
        raise ValueError(f"Primer pair source positions exceed 96-well coordinates from {start_well}.")
    return f"{PLATE_ROWS[row]}{column + 1}"


def _template_source_position(start_well: str, template_idx: int) -> str:
    return _allocate_wells(start_well, template_idx + 1)[template_idx]


def _premix_position_for_pair(pcr_design: dict, pair_idx: int) -> str:
    return _template_source_position(str(pcr_design.get("premix_start_position", "A1") or "A1"), pair_idx)


def _worklist_row(
    step: int,
    action: str,
    source_labware: str,
    source_position: str,
    dest_labware: str,
    dest_position: str,
    volume: float,
    liquid_class: str,
    notes: str,
    tip_strategy: str = "new_tip_each_transfer",
    mix_after: str = "No",
) -> dict:
    return {
        "Step": step,
        "Action": action,
        "SourceLabware": source_labware,
        "SourcePosition": source_position,
        "DestLabware": dest_labware,
        "DestPosition": dest_position,
        "Volume_uL": volume,
        "LiquidClass": liquid_class,
        "TipStrategy": tip_strategy,
        "MixAfter": mix_after,
        "Notes": notes,
    }
