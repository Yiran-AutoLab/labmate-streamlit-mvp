from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from agents.llm_client import load_saved_llm_settings, resolve_api_key, save_llm_settings
from agents.llm_reviewer import review_layout
from models.plate_models import (
    PLATE_LAYOUT_COLUMNS,
    TRANSFER_COLUMNS,
    VALIDATION_COLUMNS,
    WELL_CONTENT_COLUMNS,
)
from outputs.excel_writer import build_excel_workbook
from services.correction_service import (
    apply_confirmed_correction,
    build_correction_context as service_build_correction_context,
    interpret_correction_command,
    summarize_dataframe_for_llm,
)
from services.experiment_service import (
    generate_draft_experiment,
    save_source_edits as service_save_source_edits,
    save_table_edits,
    sync_sources_from_well_contents as service_sync_sources_from_well_contents,
)
from services.transfer_service import (
    filtered_transfer_preview as service_filtered_transfer_preview,
    regenerate_derived_tables as service_regenerate_derived_tables,
    source_volume_summary as service_source_volume_summary,
    transfer_preview_table as service_transfer_preview_table,
)
from services.validation_service import (
    run_validation as service_run_validation,
    validation_passed,
)
from workflows.layout_generator import build_plate_matrix


APP_DIR = Path(__file__).parent
EXAMPLE_PATH = APP_DIR / "examples" / "pcr_example.txt"


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.8rem;
            max-width: 1500px;
        }
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 0.65rem 0.75rem;
        }
        div[data-testid="stMetric"] label {
            color: #64748b;
        }
        .labmate-section-title {
            margin: 0.4rem 0 0.45rem 0;
            padding: 0.7rem 0.85rem;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            background: #ffffff;
            font-weight: 700;
            color: #111827;
        }
        .transfer-arrow {
            height: 410px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #475569;
            font-size: 2.1rem;
            font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_example_protocol() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def initialize_state() -> None:
    saved_llm = load_saved_llm_settings()
    defaults = {
        "protocol": load_example_protocol(),
        "spec": None,
        "plate_layout": pd.DataFrame(columns=PLATE_LAYOUT_COLUMNS),
        "well_contents": pd.DataFrame(columns=WELL_CONTENT_COLUMNS),
        "source_map": pd.DataFrame(),
        "summary_matrix": pd.DataFrame(),
        "validation_report": pd.DataFrame(columns=VALIDATION_COLUMNS),
        "transfer_table": pd.DataFrame(columns=TRANSFER_COLUMNS),
        "diff": pd.DataFrame(),
        "correction_command": "",
        "parsed_operations": [],
        "pending_operations": [],
        "pending_diff": pd.DataFrame(),
        "correction_chat": [],
        "original_protocol": "",
        "initial_plate_layout": pd.DataFrame(columns=PLATE_LAYOUT_COLUMNS),
        "initial_well_contents": pd.DataFrame(columns=WELL_CONTENT_COLUMNS),
        "initial_source_map": pd.DataFrame(),
        "edit_history": [],
        "last_correction_context": {},
        "ai_review": "",
        "confirmed": False,
        "llm_provider": saved_llm.get("provider", "OpenRouter"),
        "llm_model": saved_llm.get("model", "openrouter/free"),
        "llm_api_key": saved_llm.get("api_key", ""),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def regenerate_derived_tables() -> None:
    result = service_regenerate_derived_tables(
        st.session_state.plate_layout,
        st.session_state.well_contents,
        st.session_state.spec,
    )
    for key, value in result.items():
        st.session_state[key] = value


def run_validation() -> pd.DataFrame:
    regenerate_derived_tables()
    result = service_run_validation(
        st.session_state.plate_layout,
        st.session_state.well_contents,
        st.session_state.source_map,
        st.session_state.spec,
    )
    st.session_state.validation_report = result["validation_report"]
    st.session_state.transfer_table = result["transfer_table"]
    st.session_state.confirmed = False
    return result["validation_report"]


def transfer_preview_table() -> pd.DataFrame:
    return service_transfer_preview_table(st.session_state.well_contents, st.session_state.spec)


def filtered_transfer_preview(liquids: list[str], wells: list[str]) -> pd.DataFrame:
    return service_filtered_transfer_preview(
        st.session_state.well_contents,
        st.session_state.spec,
        liquids,
        wells,
    )


def show_transfer_metrics(preview: pd.DataFrame) -> None:
    used_wells = st.session_state.plate_layout["well"].astype(str).nunique()
    liquid_count = st.session_state.well_contents["liquid_name"].astype(str).nunique()
    total_volume = pd.to_numeric(st.session_state.well_contents["volume_ul"], errors="coerce").fillna(0).sum()
    source_available = pd.to_numeric(
        st.session_state.source_map.get("available_volume_ul", pd.Series(dtype=float)),
        errors="coerce",
    ).fillna(0).sum()
    validation_state = "Passed" if validation_passed(st.session_state.validation_report) else "Needs review"
    metric_cols = st.columns(5)
    metric_cols[0].metric("Target wells", used_wells)
    metric_cols[1].metric("Liquids", liquid_count)
    metric_cols[2].metric("Transfer steps", len(preview))
    metric_cols[3].metric("Source volume", f"{source_available:g} uL")
    metric_cols[4].metric("Validation", validation_state)
    st.caption(f"Total planned destination volume: {total_volume:g} uL")


def build_source_plate_matrix(source_map: pd.DataFrame, source_labware: str) -> pd.DataFrame:
    matrix = pd.DataFrame("", index=list("ABCDEFGH"), columns=[str(column) for column in range(1, 13)])
    if source_map.empty:
        return matrix

    filtered = source_map[source_map["source_labware"].astype(str) == str(source_labware)]
    for row in filtered.itertuples(index=False):
        source_well = str(row.source_well).strip().upper()
        if len(source_well) < 2 or source_well[0] not in matrix.index:
            continue
        column = source_well[1:]
        if column not in matrix.columns:
            continue
        available = pd.to_numeric(row.available_volume_ul, errors="coerce")
        required = pd.to_numeric(row.required_volume_ul, errors="coerce")
        available_text = "?" if pd.isna(available) or available <= 0 else f"{available:g}"
        required_text = "?" if pd.isna(required) else f"{required:g}"
        matrix.loc[source_well[0], column] = f"{row.liquid_name}\n{available_text}/{required_text} uL"
    return matrix


def style_plate_matrix(matrix: pd.DataFrame, color: str):
    def cell_style(value: object) -> str:
        if str(value).strip():
            return f"background-color: {color}; color: #111827; font-weight: 650;"
        return "background-color: #ffffff; color: #94a3b8;"

    return (
        matrix.style.map(cell_style)
        .set_properties(
            **{
                "text-align": "center",
                "white-space": "pre-wrap",
                "font-size": "12px",
                "line-height": "1.25",
            }
        )
        .set_table_styles(
            [
                {"selector": "th", "props": [("text-align", "center"), ("font-weight", "700")]},
                {"selector": "td", "props": [("border", "1px solid #e5e7eb")]},
            ]
        )
    )


def source_volume_summary(source_map: pd.DataFrame) -> pd.DataFrame:
    return service_source_volume_summary(source_map)


def display_source_target_overview() -> None:
    preview = transfer_preview_table()
    st.subheader("Transfer Workspace")
    show_transfer_metrics(preview)

    all_liquids = sorted(st.session_state.well_contents["liquid_name"].dropna().astype(str).unique().tolist())
    all_wells = sorted(st.session_state.plate_layout["well"].dropna().astype(str).unique().tolist())
    filter_cols = st.columns([1.3, 1.3, 1])
    selected_liquids = filter_cols[0].multiselect("Filter by source liquid", all_liquids)
    selected_wells = filter_cols[1].multiselect("Filter by target well", all_wells)
    selected_well = filter_cols[2].selectbox("Inspect target", all_wells)

    source_labwares = sorted(st.session_state.source_map["source_labware"].dropna().astype(str).unique().tolist())
    selected_source_labware = source_labwares[0] if source_labwares else ""

    source_col, arrow_col, target_col = st.columns([1, 0.12, 1])
    with source_col:
        st.markdown('<div class="labmate-section-title">Source plate / labware</div>', unsafe_allow_html=True)
        if source_labwares:
            selected_source_labware = st.selectbox("Source labware", source_labwares, key="overview_source_labware")
        st.dataframe(
            style_plate_matrix(build_source_plate_matrix(st.session_state.source_map, selected_source_labware), "#ecfdf5"),
            use_container_width=True,
            height=410,
        )
    with arrow_col:
        st.markdown('<div class="transfer-arrow">-></div>', unsafe_allow_html=True)
    with target_col:
        st.markdown('<div class="labmate-section-title">Target 96-well plate</div>', unsafe_allow_html=True)
        st.dataframe(
            style_plate_matrix(build_plate_matrix(st.session_state.plate_layout), "#eff6ff"),
            use_container_width=True,
            height=410,
        )

    detail_col, source_summary_col = st.columns([1.1, 1])
    with detail_col:
        st.markdown(f"**Selected target well: {selected_well}**")
        well_rows = st.session_state.well_contents[
            st.session_state.well_contents["well"].astype(str) == str(selected_well)
        ]
        st.dataframe(well_rows, use_container_width=True, hide_index=True, height=220)
    with source_summary_col:
        st.markdown("**Source volume summary**")
        st.dataframe(
            source_volume_summary(st.session_state.source_map),
            use_container_width=True,
            hide_index=True,
            height=220,
        )

    st.markdown("**Pipetting Plan Preview**")
    filtered_preview = filtered_transfer_preview(selected_liquids, selected_wells)
    st.dataframe(
        filtered_preview,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "step": st.column_config.NumberColumn("Step", width="small"),
            "source_labware": st.column_config.TextColumn("Source labware"),
            "source_well": st.column_config.TextColumn("Source well", width="small"),
            "destination_labware": st.column_config.TextColumn("Target labware"),
            "destination_well": st.column_config.TextColumn("Target well", width="small"),
            "liquid_name": st.column_config.TextColumn("Liquid"),
            "volume_ul": st.column_config.NumberColumn("Volume (uL)", format="%.2f"),
        },
    )
    if not validation_passed(st.session_state.validation_report):
        st.warning("This is a transfer preview for human review. Formal Hamilton export is enabled only after validation passes.")
    st.info("To correct the design conversationally, open the Review Chat tab. The agent will preview its interpretation before applying any change.")


def save_source_edits(source_map: pd.DataFrame) -> None:
    spec, well_contents = service_save_source_edits(
        source_map,
        st.session_state.well_contents,
        st.session_state.spec,
    )
    st.session_state.spec = spec
    st.session_state.well_contents = well_contents


def sync_sources_from_well_contents() -> None:
    st.session_state.spec = service_sync_sources_from_well_contents(
        st.session_state.spec,
        st.session_state.well_contents,
        st.session_state.source_map,
    )


def build_correction_context(command: str) -> dict:
    return service_build_correction_context(
        command,
        original_protocol=st.session_state.original_protocol,
        initial_plate_layout=st.session_state.initial_plate_layout,
        initial_well_contents=st.session_state.initial_well_contents,
        initial_source_map=st.session_state.initial_source_map,
        current_plate_layout=st.session_state.plate_layout,
        current_well_contents=st.session_state.well_contents,
        current_source_map=st.session_state.source_map,
        edit_history=st.session_state.edit_history,
        correction_chat=st.session_state.correction_chat,
    )


def generate_draft_layout() -> None:
    result = generate_draft_experiment(
        st.session_state.protocol,
        provider=st.session_state.llm_provider,
        model=st.session_state.llm_model,
        api_key=resolve_api_key(st.session_state.llm_provider, st.session_state.llm_api_key),
    )
    for key, value in result.items():
        st.session_state[key] = value


def interpret_correction() -> None:
    result = interpret_correction_command(
        st.session_state.correction_command,
        provider=st.session_state.llm_provider,
        model=st.session_state.llm_model,
        api_key=resolve_api_key(st.session_state.llm_provider, st.session_state.llm_api_key),
        original_protocol=st.session_state.original_protocol,
        initial_plate_layout=st.session_state.initial_plate_layout,
        initial_well_contents=st.session_state.initial_well_contents,
        initial_source_map=st.session_state.initial_source_map,
        plate_layout=st.session_state.plate_layout,
        well_contents=st.session_state.well_contents,
        source_map=st.session_state.source_map,
        edit_history=st.session_state.edit_history,
        correction_chat=st.session_state.correction_chat,
    )
    st.session_state.last_correction_context = result["last_correction_context"]
    st.session_state.pending_operations = result["pending_operations"]
    st.session_state.pending_diff = result["pending_diff"]
    st.session_state.parsed_operations = result["parsed_operations"]
    st.session_state.correction_chat.extend(result["chat_messages"])


def apply_pending_correction() -> None:
    operations = st.session_state.pending_operations
    command = (st.session_state.last_correction_context or {}).get("latest_user_command", st.session_state.correction_command)
    result = apply_confirmed_correction(
        plate_layout=st.session_state.plate_layout,
        well_contents=st.session_state.well_contents,
        source_map=st.session_state.source_map,
        spec=st.session_state.spec,
        operations=operations,
        command=command,
        edit_history=st.session_state.edit_history,
    )
    for key, value in result.items():
        if key == "chat_messages":
            st.session_state.correction_chat.extend(value)
        else:
            st.session_state[key] = value


def reject_pending_correction() -> None:
    if st.session_state.pending_operations:
        st.session_state.correction_chat.append(
            {
                "role": "assistant",
                "message": "Rejected the pending correction. No table rows were changed.",
            }
        )
    st.session_state.pending_operations = []
    st.session_state.pending_diff = pd.DataFrame()


def save_direct_edits(plate_layout: pd.DataFrame, well_contents: pd.DataFrame, source_map: pd.DataFrame) -> None:
    result = save_table_edits(
        st.session_state.plate_layout,
        plate_layout,
        well_contents,
        source_map,
        st.session_state.spec,
    )
    for key, value in result.items():
        st.session_state[key] = value


def main() -> None:
    st.set_page_config(page_title="LabMate Layout Agent MVP", layout="wide")
    inject_css()
    initialize_state()

    st.title("LabMate Human-in-the-loop Plate Layout Agent")
    st.caption("General layout-first liquid-handling MVP with real LLM interpretation and deterministic validation/export.")

    with st.sidebar:
        st.header("LLM")
        st.selectbox("Provider", ["OpenRouter", "OpenAI"], key="llm_provider")
        st.text_input("Model", key="llm_model")
        st.text_input("API key", key="llm_api_key", type="password")
        remember_key = st.checkbox("Remember API key on this computer", value=bool(st.session_state.llm_api_key))
        if st.button("Save LLM Settings", use_container_width=True):
            if not st.session_state.llm_api_key:
                st.warning("Enter an API key before saving.")
            else:
                save_llm_settings(st.session_state.llm_provider, st.session_state.llm_model, st.session_state.llm_api_key)
                st.success("Saved locally. This secret file is ignored by git.")
        if remember_key and st.session_state.llm_api_key:
            save_llm_settings(st.session_state.llm_provider, st.session_state.llm_model, st.session_state.llm_api_key)
        st.divider()
        st.header("Workflow")
        st.write("1. Paste protocol")
        st.write("2. Generate draft layout")
        st.write("3. Review source and target plates")
        st.write("4. Validate")
        st.write("5. Confirm and export")
        if st.button("Load Example Protocol", use_container_width=True):
            st.session_state.protocol = load_example_protocol()
            st.rerun()
        if st.session_state.spec:
            st.subheader("Assumptions")
            for assumption in st.session_state.spec.get("assumptions", []):
                st.write(f"- {assumption}")

    st.subheader("1. Natural Language Protocol")
    st.text_area("Protocol", key="protocol", height=180)
    if st.button("Generate Draft Layout", type="primary"):
        try:
            generate_draft_layout()
        except Exception as exc:
            st.error(f"LLM draft generation failed: {exc}")

    if st.session_state.spec is None:
        st.info("Generate a draft layout to start reviewing the plate.")
        st.subheader("Review Chat")
        st.warning("Review Chat is available after a draft layout is generated, because the agent needs source/target tables to preview what would change.")
        st.text_area(
            "Message the agent",
            value="先生成 draft 后，就可以在这里说：source都放在一个板子；不对，只改master mix；把A1移到H12。",
            height=100,
            disabled=True,
        )
        return

    display_source_target_overview()

    edit_tab, correction_tab, validation_export_tab, raw_tab = st.tabs(
        [
            "Table Editor",
            "Review Chat",
            "Validate & Export",
            "Raw Review Tables",
        ]
    )

    with edit_tab:
        st.subheader("Source Setup")
        edited_source_map = st.data_editor(
            st.session_state.source_map,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="source_map_editor",
            disabled=["required_volume_ul", "status"],
            column_config={
                "available_volume_ul": st.column_config.NumberColumn("Available volume (uL)", min_value=0.0),
                "required_volume_ul": st.column_config.NumberColumn("Required volume (uL)"),
                "status": st.column_config.TextColumn("Status"),
            },
        )
        st.subheader("Target Plate Layout")
        edited_plate_layout = st.data_editor(
            st.session_state.plate_layout,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="plate_layout_editor",
        )
        st.subheader("Liquid Additions Per Well")
        edited_well_contents = st.data_editor(
            st.session_state.well_contents,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="well_contents_editor",
        )
        if st.button("Save Table Edits", type="primary"):
            save_direct_edits(edited_plate_layout, edited_well_contents, edited_source_map)
            st.success("Table edits saved and validation rerun.")
            st.rerun()

    with correction_tab:
        st.subheader("Chat With The Layout Agent")
        st.caption("The agent will interpret your message first. Nothing changes until you approve the proposed operation.")
        if st.session_state.correction_chat:
            for item in st.session_state.correction_chat[-8:]:
                with st.chat_message(item["role"]):
                    st.markdown(item["message"])
        else:
            st.info("Tell the agent what looks wrong. It will explain the proposed operation before changing any tables.")

        st.text_area(
            "Message the agent",
            key="correction_command",
            height=120,
            placeholder="例如：source都放在一个板子；不对，我的意思是只改master mix，不要动template；把A1移到H12",
        )
        with st.expander("Command examples"):
            st.markdown(
                """
                - `source都放在一个板子`
                - `把所有source合并到SourcePlate_1，从A1开始`
                - `把master mix的source改到SourcePlate_1 A1`
                - `把A1移到H12`
                - `交换A1和A2`
                - `避开A1,A2`
                - `把water体积改成6 uL`
                """
            )
        if st.button("Send To AI For Interpretation", type="primary"):
            try:
                interpret_correction()
                st.rerun()
            except Exception as exc:
                st.error(f"LLM correction parsing failed: {exc}")

        if st.session_state.pending_operations:
            st.subheader("Pending Operation")
            st.json(st.session_state.pending_operations)
            st.subheader("Preview Diff")
            if st.session_state.pending_diff.empty:
                st.warning("No rows would change. Reject this or clarify your command.")
            else:
                st.dataframe(st.session_state.pending_diff, use_container_width=True, hide_index=True)

            apply_col, reject_col = st.columns([1, 1])
            with apply_col:
                if st.button("Apply Proposed Change", type="primary", use_container_width=True):
                    try:
                        apply_pending_correction()
                        st.success("Confirmed change applied.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Applying correction failed: {exc}")
            with reject_col:
                if st.button("Reject / Try Again", use_container_width=True):
                    reject_pending_correction()
                    st.rerun()

        if st.session_state.parsed_operations and not st.session_state.pending_operations:
            st.write("Last applied operations")
            st.json(st.session_state.parsed_operations)

        st.subheader("Applied Diff View")
        if st.session_state.diff.empty:
            st.info("No applied changes to show yet.")
        else:
            st.dataframe(st.session_state.diff, use_container_width=True, hide_index=True)
        st.subheader("Experiment Memory")
        if st.button("Clear Edit History", key="clear_edit_history_chat"):
            st.session_state.edit_history = []
            st.success("Edit history cleared. Current tables were not changed.")
        with st.expander("Memory Debug View"):
            st.markdown("**Original Protocol**")
            st.text(st.session_state.original_protocol or "(not captured yet)")
            st.markdown("**Initial Draft Summary**")
            st.text(
                "Plate layout:\n"
                + summarize_dataframe_for_llm(st.session_state.initial_plate_layout, max_rows=12)
                + "\nWell contents:\n"
                + summarize_dataframe_for_llm(st.session_state.initial_well_contents, max_rows=12)
                + "\nSource map:\n"
                + summarize_dataframe_for_llm(st.session_state.initial_source_map, max_rows=12)
            )
            st.markdown("**Applied Edit History**")
            st.json(st.session_state.edit_history)
            st.markdown("**Last Correction Context**")
            st.json(st.session_state.last_correction_context)
        st.subheader("Ask AI To Review")
        if st.button("Ask AI to Review"):
            try:
                st.session_state.ai_review = review_layout(
                    st.session_state.protocol,
                    st.session_state.plate_layout,
                    st.session_state.well_contents,
                    provider=st.session_state.llm_provider,
                    model=st.session_state.llm_model,
                    api_key=resolve_api_key(st.session_state.llm_provider, st.session_state.llm_api_key),
                )
            except Exception as exc:
                st.session_state.ai_review = f"LLM review failed: {exc}"
        if st.session_state.ai_review:
            st.text(st.session_state.ai_review)

    with validation_export_tab:
        st.subheader("Validation Report")
        if st.button("Run Validation", type="primary"):
            run_validation()
            st.success("Validation complete.")
            st.rerun()
        st.dataframe(st.session_state.validation_report, use_container_width=True, hide_index=True)
        passed = validation_passed(st.session_state.validation_report)
        if passed:
            st.success("Validation passed. Hamilton transfer table can be generated.")
        else:
            st.error("Validation has blocking issues. Hamilton transfer table is gated until these pass.")

        st.subheader("Confirm Layout")
        passed = validation_passed(st.session_state.validation_report)
        st.button(
            "Confirm Layout",
            disabled=not passed,
            on_click=lambda: st.session_state.update({"confirmed": True}),
        )
        if st.session_state.confirmed:
            st.success("Layout confirmed by human reviewer.")
        st.subheader("Hamilton Transfer Table")
        if passed:
            st.dataframe(st.session_state.transfer_table, use_container_width=True, hide_index=True)
        else:
            st.warning("Transfer table is hidden until validation passes.")
        excel_bytes = build_excel_workbook(
            st.session_state.plate_layout,
            st.session_state.well_contents,
            st.session_state.source_map,
            st.session_state.transfer_table,
            st.session_state.validation_report,
            st.session_state.summary_matrix,
        )
        st.download_button(
            "Export Hamilton Table Excel",
            data=excel_bytes,
            file_name="labmate_hamilton_layout.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            disabled=not (passed and st.session_state.confirmed),
        )

    with raw_tab:
        st.subheader("Experiment Memory")
        if st.button("Clear Edit History", key="clear_edit_history_raw"):
            st.session_state.edit_history = []
            st.success("Edit history cleared. Current tables were not changed.")
        st.markdown("**Original Protocol**")
        st.text(st.session_state.original_protocol or "(not captured yet)")
        st.markdown("**Initial Draft Summary**")
        st.text(
            "Plate layout:\n"
            + summarize_dataframe_for_llm(st.session_state.initial_plate_layout, max_rows=12)
            + "\nWell contents:\n"
            + summarize_dataframe_for_llm(st.session_state.initial_well_contents, max_rows=12)
            + "\nSource map:\n"
            + summarize_dataframe_for_llm(st.session_state.initial_source_map, max_rows=12)
        )
        st.markdown("**Applied Edit History**")
        st.json(st.session_state.edit_history)
        st.markdown("**Last Correction Context**")
        st.json(st.session_state.last_correction_context)
        st.subheader("Plate Layout")
        st.dataframe(st.session_state.plate_layout, use_container_width=True, hide_index=True)
        st.subheader("Well Contents")
        st.dataframe(st.session_state.well_contents, use_container_width=True, hide_index=True)
        st.subheader("Source Map")
        st.dataframe(st.session_state.source_map, use_container_width=True, hide_index=True)
        st.subheader("Summary Matrix")
        st.dataframe(st.session_state.summary_matrix, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
