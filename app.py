from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from agents.correction_command_agent import parse_correction_command
from agents.llm_client import load_saved_llm_settings, resolve_api_key, save_llm_settings
from agents.llm_reviewer import review_layout
from agents.protocol_to_layout_agent import protocol_to_layout
from models.plate_models import (
    PLATE_LAYOUT_COLUMNS,
    SOURCE_MAP_COLUMNS,
    TRANSFER_COLUMNS,
    VALIDATION_COLUMNS,
    WELL_CONTENT_COLUMNS,
    ensure_columns,
)
from outputs.excel_writer import build_excel_workbook
from validators.layout_validator import validate_layout
from validators.source_validator import validate_sources, validation_results_to_df
from validators.volume_validator import validate_volumes
from workflows.layout_editor import apply_layout_operations, build_diff
from workflows.layout_generator import build_plate_matrix, summary_matrix
from workflows.transfer_compiler import compile_hamilton_transfers
from workflows.well_contents_generator import generate_source_map, generate_well_contents


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
    spec = st.session_state.spec
    if not spec:
        return
    st.session_state.plate_layout = ensure_columns(st.session_state.plate_layout, PLATE_LAYOUT_COLUMNS)
    st.session_state.well_contents = ensure_columns(st.session_state.well_contents, WELL_CONTENT_COLUMNS)
    st.session_state.source_map = generate_source_map(st.session_state.well_contents, spec)
    st.session_state.summary_matrix = summary_matrix(st.session_state.plate_layout)


def run_validation() -> pd.DataFrame:
    spec = st.session_state.spec or {}
    expected_total = spec.get("expected_total_volume_ul")
    regenerate_derived_tables()
    results = []
    results.extend(validate_layout(st.session_state.plate_layout))
    results.extend(validate_volumes(st.session_state.well_contents, expected_total))
    results.extend(validate_sources(st.session_state.well_contents, st.session_state.source_map))
    report = validation_results_to_df(results)
    st.session_state.validation_report = report
    passed = validation_passed(report)
    st.session_state.transfer_table = (
        compile_hamilton_transfers(st.session_state.well_contents, spec.get("destination_labware", "Plate_96"))
        if passed
        else pd.DataFrame(columns=TRANSFER_COLUMNS)
    )
    st.session_state.confirmed = False
    return report


def validation_passed(report: pd.DataFrame) -> bool:
    if report.empty:
        return False
    blocking = report[report["severity"].isin(["error", "critical"]) & (report["status"] != "PASS")]
    return blocking.empty


def transfer_preview_table() -> pd.DataFrame:
    spec = st.session_state.spec or {}
    destination_labware = spec.get("destination_labware", "Plate_96")
    return compile_hamilton_transfers(st.session_state.well_contents, destination_labware)


def filtered_transfer_preview(liquids: list[str], wells: list[str]) -> pd.DataFrame:
    preview = transfer_preview_table()
    if liquids:
        preview = preview[preview["liquid_name"].astype(str).isin(liquids)]
    if wells:
        preview = preview[preview["destination_well"].astype(str).isin(wells)]
    return preview.reset_index(drop=True)


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
    if source_map.empty:
        return pd.DataFrame(columns=["source_labware", "available_volume_ul", "required_volume_ul", "status"])
    summary = (
        source_map.assign(
            available_volume_ul=pd.to_numeric(source_map["available_volume_ul"], errors="coerce").fillna(0),
            required_volume_ul=pd.to_numeric(source_map["required_volume_ul"], errors="coerce").fillna(0),
        )
        .groupby("source_labware", dropna=False)[["available_volume_ul", "required_volume_ul"]]
        .sum()
        .reset_index()
    )
    summary["status"] = summary.apply(
        lambda row: "UNKNOWN" if row.available_volume_ul <= 0 else ("OK" if row.available_volume_ul >= row.required_volume_ul else "INSUFFICIENT"),
        axis=1,
    )
    return summary


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


def save_source_edits(source_map: pd.DataFrame) -> None:
    edited = ensure_columns(source_map, SOURCE_MAP_COLUMNS)
    spec = st.session_state.spec or {}
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
            st.session_state.well_contents["liquid_name"].astype(str).str.strip().eq(liquid_name)
            & st.session_state.well_contents["liquid_role"].astype(str).str.strip().eq(liquid_role)
        )
        st.session_state.well_contents.loc[match, "source_labware"] = source_labware
        st.session_state.well_contents.loc[match, "source_well"] = source_well
        spec_sources[f"{liquid_role}:{liquid_name}:{source_labware}:{source_well}"] = {
            "liquid_name": liquid_name,
            "liquid_role": liquid_role,
            "source_labware": source_labware,
            "source_well": source_well,
            "available_volume_ul": float(available_volume),
        }

    spec["sources"] = spec_sources
    st.session_state.spec = spec


def sync_sources_from_well_contents() -> None:
    spec = st.session_state.spec or {}
    previous_source_map = ensure_columns(st.session_state.source_map, SOURCE_MAP_COLUMNS)
    availability_by_liquid = {}
    for row in previous_source_map.itertuples(index=False):
        key = (str(row.liquid_name).strip(), str(row.liquid_role).strip())
        available_volume = pd.to_numeric(row.available_volume_ul, errors="coerce")
        availability_by_liquid[key] = 0.0 if pd.isna(available_volume) else float(available_volume)

    spec_sources = {}
    content_sources = (
        st.session_state.well_contents[
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

    spec["sources"] = spec_sources
    st.session_state.spec = spec


def generate_draft_layout() -> None:
    spec = protocol_to_layout(
        st.session_state.protocol,
        provider=st.session_state.llm_provider,
        model=st.session_state.llm_model,
        api_key=resolve_api_key(st.session_state.llm_provider, st.session_state.llm_api_key),
    )
    st.session_state.spec = spec
    st.session_state.plate_layout = spec["plate_layout"]
    st.session_state.well_contents = generate_well_contents(st.session_state.plate_layout, spec)
    st.session_state.diff = pd.DataFrame()
    st.session_state.parsed_operations = []
    st.session_state.ai_review = ""
    st.session_state.confirmed = False
    regenerate_derived_tables()
    run_validation()


def apply_correction() -> None:
    command = st.session_state.correction_command
    operations = parse_correction_command(
        command,
        provider=st.session_state.llm_provider,
        model=st.session_state.llm_model,
        api_key=resolve_api_key(st.session_state.llm_provider, st.session_state.llm_api_key),
    )
    before = st.session_state.plate_layout.copy()
    layout, contents, diff = apply_layout_operations(
        st.session_state.plate_layout,
        st.session_state.well_contents,
        operations,
    )
    st.session_state.plate_layout = layout
    st.session_state.well_contents = contents
    st.session_state.diff = diff if not diff.empty else build_diff(before, layout)
    st.session_state.parsed_operations = operations
    sync_sources_from_well_contents()
    regenerate_derived_tables()
    run_validation()


def save_direct_edits(plate_layout: pd.DataFrame, well_contents: pd.DataFrame, source_map: pd.DataFrame) -> None:
    before = st.session_state.plate_layout.copy()
    st.session_state.plate_layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS)
    st.session_state.well_contents = ensure_columns(well_contents, WELL_CONTENT_COLUMNS)
    save_source_edits(source_map)
    st.session_state.diff = build_diff(before, st.session_state.plate_layout)
    regenerate_derived_tables()
    run_validation()


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
        return

    display_source_target_overview()

    edit_tab, correction_tab, validation_export_tab, raw_tab = st.tabs(
        [
            "Table Editor",
            "AI Correction",
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
        st.subheader("Correct The Layout By Talking To The Agent")
        st.text_area(
            "Correction command",
            key="correction_command",
            height=120,
            placeholder="Examples: move A1 to H12; swap A1 and A2; avoid A1,A2; change water volume to 6 uL; fill by column",
        )
        if st.button("Apply Correction"):
            try:
                apply_correction()
                if st.session_state.diff.empty:
                    st.warning("The command was parsed, but no matching layout/content/source rows changed.")
                else:
                    st.success("Correction applied and validation rerun.")
            except Exception as exc:
                st.error(f"LLM correction parsing failed: {exc}")
        if st.session_state.parsed_operations:
            st.write("Parsed operations")
            st.json(st.session_state.parsed_operations)
        st.subheader("8. Diff View")
        if st.session_state.diff.empty:
            st.info("No layout changes to show yet.")
        else:
            st.dataframe(st.session_state.diff, use_container_width=True, hide_index=True)
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
