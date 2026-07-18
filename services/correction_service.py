from __future__ import annotations

import pandas as pd

from agents.correction_command_agent import parse_correction_command
from workflows.layout_editor import apply_layout_operations, build_diff

from services.experiment_service import build_validated_state, sync_sources_from_well_contents


def summarize_dataframe_for_llm(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df is None or df.empty:
        return "(empty table)"
    preview = df.head(max_rows)
    text = preview.to_csv(index=False)
    if len(df) > max_rows:
        text += f"\n... truncated; showing first {max_rows} of {len(df)} rows."
    return text


def summarize_diff_for_history(diff: pd.DataFrame) -> str:
    if diff is None or diff.empty:
        return "No row-level changes were detected."
    wells = []
    for column in ["well", "before_well", "after_well"]:
        if column in diff:
            wells.extend(diff[column].dropna().astype(str).tolist())
    wells = sorted({well for well in wells if well and well != "nan"})
    if wells:
        return f"{len(diff)} row-level changes were applied. Affected wells: {', '.join(wells[:20])}."
    return f"{len(diff)} row-level changes were applied."


def build_correction_context(
    command: str,
    *,
    original_protocol: str,
    initial_plate_layout: pd.DataFrame,
    initial_well_contents: pd.DataFrame,
    initial_source_map: pd.DataFrame,
    current_plate_layout: pd.DataFrame,
    current_well_contents: pd.DataFrame,
    current_source_map: pd.DataFrame,
    edit_history: list[dict],
    correction_chat: list[dict],
) -> dict:
    return {
        "latest_user_command": command,
        "original_protocol": original_protocol,
        "initial_plate_layout": summarize_dataframe_for_llm(initial_plate_layout),
        "initial_well_contents": summarize_dataframe_for_llm(initial_well_contents),
        "initial_source_map": summarize_dataframe_for_llm(initial_source_map),
        "current_plate_layout": summarize_dataframe_for_llm(current_plate_layout),
        "current_well_contents": summarize_dataframe_for_llm(current_well_contents),
        "current_source_map": summarize_dataframe_for_llm(current_source_map),
        "edit_history": edit_history,
        "recent_correction_chat": correction_chat[-8:],
    }


def summarize_operations(operations: list[dict], diff: pd.DataFrame) -> str:
    if not operations:
        return "I could not identify a concrete operation."

    lines = ["I understand this as:"]
    for operation in operations:
        op = operation.get("op", "")
        if op == "CONSOLIDATE_SOURCES":
            lines.append(
                f"- Put all source liquids into `{operation.get('source_labware', 'SourcePlate_1')}`, starting at `{operation.get('start_well', 'A1')}`."
            )
            lines.append("- Do not change the target plate layout.")
        elif op == "CHANGE_SOURCE":
            lines.append(
                f"- Move `{operation.get('liquid_name', '')}` source to `{operation.get('source_labware', '')} {operation.get('source_well', '')}`."
            )
        elif op == "CHANGE_VOLUME":
            lines.append(f"- Change `{operation.get('liquid_name', '')}` volume to `{operation.get('volume_ul', '')} uL`.")
        elif op == "MOVE_WELL":
            lines.append(f"- Move target well `{operation.get('source_well', '')}` to `{operation.get('dest_well', '')}`.")
        elif op == "SWAP_WELLS":
            lines.append(f"- Swap target wells `{operation.get('well_a', '')}` and `{operation.get('well_b', '')}`.")
        elif op == "AVOID_WELLS":
            lines.append(f"- Avoid target wells `{', '.join(operation.get('wells', []))}`.")
        elif op in {"FILL_BY_ROW", "FILL_BY_COLUMN", "GROUP_BY", "ADD_REPLICATE", "REMOVE_REPLICATE"}:
            lines.append(f"- Apply `{op}` with parameters `{operation}`.")
        else:
            lines.append(f"- Apply `{op}` with parameters `{operation}`.")

    if diff.empty:
        lines.append("")
        lines.append("Preview result: no rows would change. You may need to clarify the command.")
    else:
        lines.append("")
        lines.append(f"Preview result: `{len(diff)}` row-level changes would be made.")
    lines.append("Review this, then choose Apply or Reject.")
    return "\n".join(lines)


def interpret_correction_command(
    command: str,
    *,
    provider: str,
    model: str,
    api_key: str | None,
    original_protocol: str,
    initial_plate_layout: pd.DataFrame,
    initial_well_contents: pd.DataFrame,
    initial_source_map: pd.DataFrame,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    edit_history: list[dict],
    correction_chat: list[dict],
) -> dict:
    command = command.strip()
    if not command:
        raise ValueError("Enter a correction command first.")
    context = build_correction_context(
        command,
        original_protocol=original_protocol,
        initial_plate_layout=initial_plate_layout,
        initial_well_contents=initial_well_contents,
        initial_source_map=initial_source_map,
        current_plate_layout=plate_layout,
        current_well_contents=well_contents,
        current_source_map=source_map,
        edit_history=edit_history,
        correction_chat=correction_chat,
    )
    operations = parse_correction_command(
        command,
        context=context,
        provider=provider,
        model=model,
        api_key=api_key,
    )
    _, _, preview_diff = apply_layout_operations(
        plate_layout,
        well_contents,
        operations,
    )
    return {
        "last_correction_context": context,
        "pending_operations": operations,
        "pending_diff": preview_diff,
        "parsed_operations": operations,
        "chat_messages": [
            {"role": "user", "message": command},
            {"role": "assistant", "message": summarize_operations(operations, preview_diff)},
        ],
    }


def apply_confirmed_correction(
    *,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    source_map: pd.DataFrame,
    spec: dict | None,
    operations: list[dict],
    command: str,
    edit_history: list[dict],
) -> dict:
    if not operations:
        raise ValueError("No pending correction to apply.")
    before = plate_layout.copy()
    layout, contents, diff = apply_layout_operations(
        plate_layout,
        well_contents,
        operations,
    )
    applied_diff = diff if not diff.empty else build_diff(before, layout)
    updated_history = list(edit_history)
    updated_history.append(
        {
            "step": len(updated_history) + 1,
            "user_command": command,
            "operations": operations,
            "diff_summary": summarize_diff_for_history(applied_diff),
            "status": "applied",
        }
    )
    updated_spec = sync_sources_from_well_contents(spec, contents, source_map)
    state = build_validated_state(layout, contents, updated_spec)
    return {
        "spec": updated_spec,
        **state,
        "diff": applied_diff,
        "edit_history": updated_history,
        "parsed_operations": operations,
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "chat_messages": [
            {
                "role": "assistant",
                "message": "Applied the confirmed correction and reran validation.",
            }
        ],
        "confirmed": False,
    }
