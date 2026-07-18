from __future__ import annotations

import pandas as pd

from agents.protocol_to_layout_agent import mock_protocol_to_layout, protocol_to_layout
from models.plate_models import PLATE_LAYOUT_COLUMNS, SOURCE_MAP_COLUMNS, WELL_CONTENT_COLUMNS, ensure_columns, is_valid_well, normalize_well
from workflows.layout_editor import build_combined_diff, build_diff
from workflows.well_contents_generator import generate_well_contents

from services.transfer_service import regenerate_derived_tables
from services.validation_service import run_validation


def build_validated_state(
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    spec: dict | None,
) -> dict:
    derived = regenerate_derived_tables(plate_layout, well_contents, spec)
    validation = run_validation(
        derived["plate_layout"],
        derived["well_contents"],
        derived["source_map"],
        spec,
    )
    return {**derived, **validation}


def generate_draft_experiment(
    protocol: str,
    *,
    provider: str,
    model: str,
    api_key: str | None,
) -> dict:
    if provider == "Mock":
        spec = mock_protocol_to_layout(protocol)
    else:
        spec = protocol_to_layout(
            protocol,
            provider=provider,
            model=model,
            api_key=api_key,
        )
    plate_layout = spec["plate_layout"]
    well_contents = generate_well_contents(plate_layout, spec)
    state = build_validated_state(plate_layout, well_contents, spec)
    return {
        "spec": spec,
        **state,
        "diff": pd.DataFrame(),
        "parsed_operations": [],
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "correction_chat": [],
        "ai_review": "",
        "confirmed": False,
        "original_protocol": protocol,
        "initial_plate_layout": state["plate_layout"].copy(),
        "initial_well_contents": state["well_contents"].copy(),
        "initial_source_map": state["source_map"].copy(),
        "edit_history": [],
        "last_correction_context": {},
    }


def manually_edit_plate_well(
    *,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    spec: dict | None,
    original_well: str,
    well: str,
    label: str,
    sample: str,
    condition: str,
    replicate: int,
    assay_type: str,
    edit_history: list[dict],
) -> dict:
    before_layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS)
    before_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    original_well = normalize_well(original_well)
    destination_well = normalize_well(well)

    if not is_valid_well(destination_well):
        raise ValueError(f"Invalid destination well: {destination_well}. Use A1-H12.")
    original_mask = before_layout["well"].astype(str).str.upper().eq(original_well)
    if not original_mask.any():
        raise ValueError(f"Destination well {original_well} is not occupied.")
    occupied = before_layout["well"].astype(str).str.upper().eq(destination_well) & ~original_mask
    if occupied.any():
        raise ValueError(f"Destination well {destination_well} is already occupied.")

    layout = before_layout.copy()
    contents = before_contents.copy()
    cleaned_label = str(label).strip()
    layout.loc[original_mask, ["well", "label", "sample", "condition", "replicate", "assay_type"]] = [
        destination_well,
        cleaned_label,
        str(sample).strip(),
        str(condition).strip(),
        int(replicate),
        str(assay_type).strip(),
    ]
    content_mask = contents["well"].astype(str).str.upper().eq(original_well)
    contents.loc[content_mask, "well"] = destination_well
    contents.loc[content_mask, "label"] = cleaned_label

    diff = build_combined_diff(before_layout, layout, before_contents, contents)
    updated_history = list(edit_history)
    updated_history.append(
        {
            "step": len(updated_history) + 1,
            "user_command": f"Manual plate edit: {original_well} -> {destination_well}",
            "operations": [
                {
                    "op": "MANUAL_EDIT_WELL",
                    "original_well": original_well,
                    "well": destination_well,
                    "label": cleaned_label,
                    "sample": str(sample).strip(),
                    "condition": str(condition).strip(),
                    "replicate": int(replicate),
                    "assay_type": str(assay_type).strip(),
                }
            ],
            "diff_summary": f"Manually edited {original_well}; current destination is {destination_well}.",
            "status": "applied",
        }
    )
    state = build_validated_state(layout, contents, spec)
    return {
        **state,
        "diff": diff,
        "edit_history": updated_history,
        "parsed_operations": [],
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "confirmed": False,
    }


def manually_edit_source(
    *,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    spec: dict | None,
    liquid_name: str,
    liquid_role: str,
    original_source_labware: str,
    original_source_well: str,
    source_labware: str,
    source_well: str,
    available_volume_ul: float,
    edit_history: list[dict],
) -> dict:
    layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS)
    before_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    previous_source_map = ensure_columns(source_map, SOURCE_MAP_COLUMNS)
    original_source_well = normalize_well(original_source_well)
    updated_source_well = normalize_well(source_well)
    updated_source_labware = str(source_labware).strip()

    if not updated_source_labware:
        raise ValueError("Source labware cannot be empty.")
    if not is_valid_well(updated_source_well):
        raise ValueError(f"Invalid source well: {updated_source_well}. Use A1-H12.")
    if float(available_volume_ul) < 0:
        raise ValueError("Available source volume cannot be negative.")

    match = (
        before_contents["liquid_name"].astype(str).str.strip().eq(str(liquid_name).strip())
        & before_contents["liquid_role"].astype(str).str.strip().eq(str(liquid_role).strip())
        & before_contents["source_labware"].astype(str).str.strip().eq(str(original_source_labware).strip())
        & before_contents["source_well"].astype(str).str.upper().eq(original_source_well)
    )
    if not match.any():
        raise ValueError("The selected source is no longer present in the current well contents.")

    contents = before_contents.copy()
    contents.loc[match, "source_labware"] = updated_source_labware
    contents.loc[match, "source_well"] = updated_source_well

    edited_source_map = previous_source_map.copy()
    source_match = (
        edited_source_map["liquid_name"].astype(str).str.strip().eq(str(liquid_name).strip())
        & edited_source_map["liquid_role"].astype(str).str.strip().eq(str(liquid_role).strip())
        & edited_source_map["source_labware"].astype(str).str.strip().eq(str(original_source_labware).strip())
        & edited_source_map["source_well"].astype(str).str.upper().eq(original_source_well)
    )
    edited_source_map.loc[source_match, "source_labware"] = updated_source_labware
    edited_source_map.loc[source_match, "source_well"] = updated_source_well
    edited_source_map.loc[source_match, "available_volume_ul"] = float(available_volume_ul)
    updated_spec = sync_sources_from_well_contents(spec, contents, edited_source_map)

    diff = build_combined_diff(layout, layout, before_contents, contents)
    updated_history = list(edit_history)
    updated_history.append(
        {
            "step": len(updated_history) + 1,
            "user_command": f"Manual source edit: {liquid_name}",
            "operations": [
                {
                    "op": "MANUAL_EDIT_SOURCE",
                    "liquid_name": str(liquid_name).strip(),
                    "source_labware": updated_source_labware,
                    "source_well": updated_source_well,
                    "available_volume_ul": float(available_volume_ul),
                }
            ],
            "diff_summary": (
                f"Moved source {liquid_name} from {original_source_labware} {original_source_well} "
                f"to {updated_source_labware} {updated_source_well}."
            ),
            "status": "applied",
        }
    )
    state = build_validated_state(layout, contents, updated_spec)
    return {
        "spec": updated_spec,
        **state,
        "diff": diff,
        "edit_history": updated_history,
        "parsed_operations": [],
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "confirmed": False,
    }


def manually_edit_volumes(
    *,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    spec: dict | None,
    updates: list[dict],
    edit_history: list[dict],
) -> dict:
    if not updates:
        raise ValueError("No volume updates were provided.")

    layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS)
    before_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    contents = before_contents.copy()
    changed_rows: list[dict] = []

    for update in updates:
        well = normalize_well(update.get("well", ""))
        source_well = normalize_well(update.get("source_well", ""))
        volume_ul = float(update.get("volume_ul", 0))
        if volume_ul < 0:
            raise ValueError("Transfer volume cannot be negative.")
        match = (
            contents["well"].astype(str).str.upper().eq(well)
            & contents["liquid_name"].astype(str).str.strip().eq(str(update.get("liquid_name", "")).strip())
            & contents["liquid_role"].astype(str).str.strip().eq(str(update.get("liquid_role", "")).strip())
            & contents["source_labware"].astype(str).str.strip().eq(str(update.get("source_labware", "")).strip())
            & contents["source_well"].astype(str).str.upper().eq(source_well)
        )
        if not match.any():
            raise ValueError(
                f"Transfer row not found for {update.get('liquid_name', '')} -> {well}."
            )
        contents.loc[match, "volume_ul"] = volume_ul
        changed_rows.append(
            {
                "well": well,
                "liquid_name": str(update.get("liquid_name", "")).strip(),
                "volume_ul": volume_ul,
            }
        )

    updated_spec = sync_sources_from_well_contents(spec, contents, source_map)
    diff = build_combined_diff(layout, layout, before_contents, contents)
    updated_history = list(edit_history)
    updated_history.append(
        {
            "step": len(updated_history) + 1,
            "user_command": "Manual transfer volume edit",
            "operations": [{"op": "MANUAL_EDIT_VOLUMES", "updates": changed_rows}],
            "diff_summary": f"Manually changed {len(changed_rows)} transfer volume row(s).",
            "status": "applied",
        }
    )
    state = build_validated_state(layout, contents, updated_spec)
    return {
        "spec": updated_spec,
        **state,
        "diff": diff,
        "edit_history": updated_history,
        "parsed_operations": [],
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "confirmed": False,
    }


def save_source_edits(
    source_map: pd.DataFrame,
    well_contents: pd.DataFrame,
    spec: dict | None,
) -> tuple[dict, pd.DataFrame]:
    edited = ensure_columns(source_map, SOURCE_MAP_COLUMNS)
    updated_spec = dict(spec or {})
    updated_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    spec_sources = {}

    for row in edited.itertuples(index=False):
        liquid_name = str(row.liquid_name).strip()
        liquid_role = str(row.liquid_role).strip()
        source_labware = str(row.source_labware).strip()
        source_well = str(row.source_well).strip().upper()
        available_volume = pd.to_numeric(row.available_volume_ul, errors="coerce")
        if pd.isna(available_volume):
            available_volume = 0.0

        match = (
            updated_contents["liquid_name"].astype(str).str.strip().eq(liquid_name)
            & updated_contents["liquid_role"].astype(str).str.strip().eq(liquid_role)
        )
        updated_contents.loc[match, "source_labware"] = source_labware
        updated_contents.loc[match, "source_well"] = source_well
        spec_sources[f"{liquid_role}:{liquid_name}:{source_labware}:{source_well}"] = {
            "liquid_name": liquid_name,
            "liquid_role": liquid_role,
            "source_labware": source_labware,
            "source_well": source_well,
            "available_volume_ul": float(available_volume),
        }

    updated_spec["sources"] = spec_sources
    return updated_spec, updated_contents


def sync_sources_from_well_contents(
    spec: dict | None,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
) -> dict:
    updated_spec = dict(spec or {})
    previous_source_map = ensure_columns(source_map, SOURCE_MAP_COLUMNS)
    availability_by_liquid = {}
    for row in previous_source_map.itertuples(index=False):
        key = (str(row.liquid_name).strip(), str(row.liquid_role).strip())
        available_volume = pd.to_numeric(row.available_volume_ul, errors="coerce")
        availability_by_liquid[key] = 0.0 if pd.isna(available_volume) else float(available_volume)

    spec_sources = {}
    content_sources = (
        well_contents[
            ["liquid_name", "liquid_role", "source_labware", "source_well"]
        ]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    for row in content_sources.itertuples(index=False):
        liquid_name = str(row.liquid_name).strip()
        liquid_role = str(row.liquid_role).strip()
        source_labware = str(row.source_labware).strip()
        source_well = str(row.source_well).strip().upper()
        available_volume = availability_by_liquid.get((liquid_name, liquid_role), 0.0)
        spec_sources[f"{liquid_role}:{liquid_name}:{source_labware}:{source_well}"] = {
            "liquid_name": liquid_name,
            "liquid_role": liquid_role,
            "source_labware": source_labware,
            "source_well": source_well,
            "available_volume_ul": available_volume,
        }

    updated_spec["sources"] = spec_sources
    return updated_spec


def save_table_edits(
    current_plate_layout: pd.DataFrame,
    edited_plate_layout: pd.DataFrame,
    edited_well_contents: pd.DataFrame,
    edited_source_map: pd.DataFrame,
    spec: dict | None,
) -> dict:
    before = current_plate_layout.copy()
    plate_layout = ensure_columns(edited_plate_layout, PLATE_LAYOUT_COLUMNS)
    well_contents = ensure_columns(edited_well_contents, WELL_CONTENT_COLUMNS)
    updated_spec, updated_contents = save_source_edits(edited_source_map, well_contents, spec)
    state = build_validated_state(plate_layout, updated_contents, updated_spec)
    return {
        "spec": updated_spec,
        **state,
        "diff": build_diff(before, state["plate_layout"]),
        "confirmed": False,
    }
