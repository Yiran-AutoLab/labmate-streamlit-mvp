from __future__ import annotations

from labmate.agent_tools import (
    build_generic_pcr_design_from_slots_tool,
    build_pcr_config_from_slots_tool,
    build_pcr_json_tool,
    calculate_common_mix_tool,
    expand_pcr_reaction_table_tool,
    generate_pcr_plate_map_from_reaction_table_tool,
    generate_pcr_plate_map_tool,
    generate_pcr_worklist_from_reaction_table_tool,
    generate_pcr_worklist_tool,
    parse_pcr_config_tool,
    run_od600_pipeline_tool,
    validate_pcr_reaction_table_tool,
    validate_pcr_setup_tool,
)


TOOL_REGISTRY = {
    "build_generic_pcr_design_from_slots": build_generic_pcr_design_from_slots_tool,
    "expand_pcr_reaction_table": expand_pcr_reaction_table_tool,
    "generate_pcr_plate_map_from_reaction_table": generate_pcr_plate_map_from_reaction_table_tool,
    "generate_pcr_worklist_from_reaction_table": generate_pcr_worklist_from_reaction_table_tool,
    "validate_pcr_reaction_table": validate_pcr_reaction_table_tool,
    "build_pcr_config_from_slots": build_pcr_config_from_slots_tool,
    "parse_pcr_config": parse_pcr_config_tool,
    "build_pcr_json": build_pcr_json_tool,
    "calculate_common_mix": calculate_common_mix_tool,
    "generate_pcr_plate_map": generate_pcr_plate_map_tool,
    "generate_pcr_worklist": generate_pcr_worklist_tool,
    "validate_pcr_setup": validate_pcr_setup_tool,
    "run_od600_pipeline": run_od600_pipeline_tool,
}
