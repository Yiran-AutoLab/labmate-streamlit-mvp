from __future__ import annotations

from typing import Any

from labmate.pcr_setup import (
    PCRSetupConfig,
    build_generic_pcr_design_from_slots,
    build_pcr_json,
    calculate_common_mix_components,
    calculate_common_mix_components_from_reaction_table,
    calculate_common_mix_from_reaction_table,
    calculate_common_mix_volume,
    expand_pcr_design_to_reaction_table,
    generate_pcr_plate_map_from_reaction_table,
    generate_pcr_plate_map,
    generate_pcr_worklist_from_reaction_table,
    generate_pcr_worklist,
    validate_pcr_reaction_table,
    validate_pcr_setup,
)


Context = dict[str, Any]


def parse_pcr_config_tool(context: Context) -> Context:
    parse_pcr_config = context.get("parse_pcr_config_fn")
    if parse_pcr_config is None:
        raise ValueError("parse_pcr_config_fn is required in context.")
    context["config"] = parse_pcr_config(context["description"])
    context["experiment_type"] = "PCR_SETUP"
    return context


def build_pcr_config_from_slots_tool(context: Context) -> Context:
    slots = context.get("slots") or {}
    if not isinstance(slots, dict):
        raise ValueError("slots must be a dictionary.")

    template_count = _slot_int(slots, "template_count", 24)
    primer_pair_count = _slot_int(slots, "primer_pair_count", 1)
    reaction_volume_uL = _slot_float(slots, "reaction_volume_uL", 20.0)
    template_volume_uL = _slot_float(slots, "template_volume_uL", 1.0)
    enzyme_volume_uL = _slot_float(slots, "enzyme_volume_uL", 0.0)
    water_volume_uL = _slot_float(slots, "water_volume_uL", 0.0)
    forward_primer_volume_uL = _slot_float(slots, "forward_primer_volume_uL", _slot_float(slots, "primer_each_volume_uL", 0.0))
    reverse_primer_volume_uL = _slot_float(slots, "reverse_primer_volume_uL", _slot_float(slots, "primer_each_volume_uL", 0.0))
    component_common_mix = enzyme_volume_uL + water_volume_uL + forward_primer_volume_uL + reverse_primer_volume_uL
    common_mix_volume_uL = _slot_float(
        slots,
        "common_mix_volume_uL",
        component_common_mix if component_common_mix > 0 else reaction_volume_uL - template_volume_uL,
    )

    forward_labware = _slot_str(slots, "forward_primer_source_labware", "Reservoir")
    forward_position = _slot_str(slots, "forward_primer_source_position", "A3")
    reverse_labware = _slot_str(slots, "reverse_primer_source_labware", "Reservoir")
    reverse_position = _slot_str(slots, "reverse_primer_source_position", "A4")
    primer_pairs = []
    for pair_idx in range(primer_pair_count):
        primer_pairs.append(
            {
                "primer_pair_id": f"PrimerPair_{pair_idx + 1}",
                "forward_source_labware": forward_labware,
                "forward_source_position": _primer_pair_source_position(forward_position, pair_idx),
                "reverse_source_labware": reverse_labware,
                "reverse_source_position": _primer_pair_source_position(reverse_position, pair_idx),
            }
        )

    destination_layout = _slot_str(slots, "destination_layout", "auto")
    if destination_layout in {"horizontal", "row", "numbers_change_horizontal"}:
        destination_layout = "horizontal_by_primer"
    elif destination_layout in {"vertical", "column", "letters_change_vertical"}:
        destination_layout = "vertical_by_primer"

    config = PCRSetupConfig(
        reaction_volume_uL=reaction_volume_uL,
        common_mix_volume_uL=common_mix_volume_uL,
        template_volume_uL=template_volume_uL,
        number_of_samples=template_count * primer_pair_count,
        ntc_replicates=_slot_int(slots, "ntc_replicates", 0),
        common_mix_source_labware=_slot_str(slots, "common_mix_source_labware", "Reservoir_1"),
        common_mix_source_position=_slot_str(slots, "common_mix_source_position", "A1"),
        template_source_labware=_slot_str(slots, "template_source_labware", "SampleRack_1"),
        template_start_position=_slot_str(slots, "template_start_position", "B1"),
        ntc_water_source_labware=_slot_str(slots, "ntc_water_source_labware", "Reservoir_1"),
        ntc_water_source_position=_slot_str(slots, "ntc_water_source_position", "A2"),
        enzyme_volume_uL=enzyme_volume_uL,
        water_volume_uL=water_volume_uL,
        forward_primer_volume_uL=forward_primer_volume_uL,
        reverse_primer_volume_uL=reverse_primer_volume_uL,
        enzyme_source_labware=_slot_str(slots, "enzyme_source_labware", "Reservoir"),
        enzyme_source_position=_slot_str(slots, "enzyme_source_position", "A1"),
        water_source_labware=_slot_str(slots, "water_source_labware", "Reservoir"),
        water_source_position=_slot_str(slots, "water_source_position", "A2"),
        forward_primer_source_labware=forward_labware,
        forward_primer_source_position=forward_position,
        reverse_primer_source_labware=reverse_labware,
        reverse_primer_source_position=reverse_position,
        premix_labware=_slot_str(slots, "premix_labware", "PCR_premix_tube"),
        premix_position=_slot_str(slots, "premix_position", "A1"),
        premix_first=_slot_bool(slots, "premix_first", primer_pair_count > 1 or component_common_mix > 0),
        destination_labware=_slot_str(slots, "destination_labware", "PCR_plate_96"),
        destination_start_well=_slot_str(slots, "destination_start_well", "A1"),
        overage_percent=_slot_float(slots, "overage_percent", 10.0),
    )

    extra_fields = {
        "template_count": template_count,
        "primer_pair_count": primer_pair_count,
        "primer_pairs": primer_pairs,
        "destination_layout": destination_layout,
        "destination_orientation": "numbers_change_horizontal"
        if destination_layout != "vertical_by_primer"
        else "letters_change_vertical",
    }
    for field_name, value in extra_fields.items():
        setattr(config, field_name, value)

    context["config"] = config
    context["experiment_type"] = "PCR_SETUP"
    return context


def build_generic_pcr_design_from_slots_tool(context: Context) -> Context:
    plan = context.get("agent_plan", {})
    slots = context.get("slots") or {}
    if not isinstance(slots, dict):
        raise ValueError("slots must be a dictionary.")

    if isinstance(plan, dict) and plan.get("mode") == "reaction_table":
        pcr_design = {
            "task_type": "PCR_SETUP",
            "mode": "reaction_table",
            "reagents": plan.get("reagents", []),
            "reaction_recipe": plan.get("reaction_recipe", []),
            "reaction_design": plan.get("reaction_design", {}),
            "plate_layout": plan.get("plate_layout", {}),
            "operations": plan.get("operations", []),
            "missing_fields": plan.get("missing_fields", []),
        }
        if "reaction_volume_uL" in slots:
            pcr_design["reaction_volume_uL"] = slots["reaction_volume_uL"]
        if "overage_percent" in slots:
            pcr_design["overage_percent"] = slots["overage_percent"]
    else:
        pcr_design = build_generic_pcr_design_from_slots(slots)

    context["pcr_design"] = pcr_design
    context["pcr_json"] = pcr_design
    context["experiment_type"] = "PCR_SETUP"
    context["pcr_reaction_table_pending_confirmation"] = True
    return context


def expand_pcr_reaction_table_tool(context: Context) -> Context:
    reaction_table = expand_pcr_design_to_reaction_table(context["pcr_design"])
    context["reaction_table"] = reaction_table
    context["plate_map"] = generate_pcr_plate_map_from_reaction_table(reaction_table, context["pcr_design"])
    context["common_mix_volume"] = calculate_common_mix_from_reaction_table(reaction_table, context["pcr_design"])
    context["common_mix_components"] = calculate_common_mix_components_from_reaction_table(
        reaction_table,
        context["pcr_design"],
    )
    return context


def generate_pcr_plate_map_from_reaction_table_tool(context: Context) -> Context:
    context["plate_map"] = generate_pcr_plate_map_from_reaction_table(context["reaction_table"], context["pcr_design"])
    return context


def generate_pcr_worklist_from_reaction_table_tool(context: Context) -> Context:
    context["hamilton_worklist"] = generate_pcr_worklist_from_reaction_table(
        context["reaction_table"],
        context["pcr_design"],
    )
    context["pcr_reaction_table_pending_confirmation"] = False
    return context


def validate_pcr_reaction_table_tool(context: Context) -> Context:
    context["validation_report"] = validate_pcr_reaction_table(context["reaction_table"], context.get("pcr_design", {}))
    return context


def build_pcr_json_tool(context: Context) -> Context:
    context["pcr_json"] = build_pcr_json(context["config"])
    return context


def calculate_common_mix_tool(context: Context) -> Context:
    config = context["config"]
    context["common_mix_volume"] = calculate_common_mix_volume(config)
    context["common_mix_components"] = calculate_common_mix_components(config)
    return context


def generate_pcr_plate_map_tool(context: Context) -> Context:
    context["plate_map"] = generate_pcr_plate_map(context["config"])
    return context


def generate_pcr_worklist_tool(context: Context) -> Context:
    context["hamilton_worklist"] = generate_pcr_worklist(context["config"])
    return context


def validate_pcr_setup_tool(context: Context) -> Context:
    context["validation_report"] = validate_pcr_setup(context["config"])
    return context


def run_od600_pipeline_tool(context: Context) -> Context:
    run_od600_pipeline = context.get("run_od600_pipeline_fn")
    if run_od600_pipeline is None:
        raise ValueError("run_od600_pipeline_fn is required in context.")
    results = run_od600_pipeline(
        context["description"],
        context["fallback_design"],
        parser_provider=context.get("parser_provider", "rules"),
        api_key=context.get("api_key"),
        model=context.get("model"),
    )
    context.update(results)
    return context


def _slot_float(slots: dict[str, Any], key: str, default: float) -> float:
    value = slots.get(key, default)
    if value in {None, ""}:
        return float(default)
    return float(value)


def _slot_int(slots: dict[str, Any], key: str, default: int) -> int:
    return int(_slot_float(slots, key, float(default)))


def _slot_str(slots: dict[str, Any], key: str, default: str) -> str:
    value = slots.get(key, default)
    if value in {None, ""}:
        return default
    return str(value)


def _slot_bool(slots: dict[str, Any], key: str, default: bool) -> bool:
    value = slots.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _primer_pair_source_position(start_well: str, pair_idx: int) -> str:
    rows = "ABCDEFGH"
    import re

    match = re.fullmatch(r"([A-Ha-h])([1-9]|1[0-2])", start_well.strip())
    if not match:
        return start_well
    start_row = rows.index(match.group(1).upper())
    start_column = int(match.group(2))
    row_position = start_row + pair_idx
    row = row_position % len(rows)
    column = start_column + (row_position // len(rows)) * 2
    if column > 12:
        raise ValueError(f"Primer pair source positions exceed 96-well coordinates from {start_well}.")
    return f"{rows[row]}{column}"
