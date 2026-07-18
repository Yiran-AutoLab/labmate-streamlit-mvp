from __future__ import annotations

import pandas as pd

from agents.protocol_to_layout_agent import _normalize_llm_layout_payload, mock_protocol_to_layout
from models.plate_models import SOURCE_MAP_COLUMNS, TRANSFER_COLUMNS, VALIDATION_COLUMNS
from services import experiment_service
from services.correction_service import apply_confirmed_correction
from services.experiment_service import build_validated_state, manually_edit_plate_well, manually_edit_source, manually_edit_volumes
from services.transfer_service import regenerate_derived_tables
from services.validation_service import run_validation, validation_passed


PROTOCOL = """
There are 12 templates T1-T12 and 4 primer pairs P1-P4.
T1-T6 should use P1 and P2. T7-T12 should use P1-P4.
Each reaction is 20 uL: enzyme 9 uL, water 5 uL, forward primer 1 uL,
reverse primer 1 uL, template 4 uL.
"""


def generated_state(monkeypatch):
    def mock_protocol_to_layout_with_api_args(protocol: str, **_kwargs):
        return mock_protocol_to_layout(protocol)

    monkeypatch.setattr(experiment_service, "protocol_to_layout", mock_protocol_to_layout_with_api_args)
    return experiment_service.generate_draft_experiment(
        PROTOCOL,
        provider="Mock",
        model="mock",
        api_key=None,
    )


def test_initial_plate_generation(monkeypatch):
    state = generated_state(monkeypatch)

    assert not state["plate_layout"].empty
    assert not state["well_contents"].empty
    assert not state["source_map"].empty
    assert list(state["validation_report"].columns) == VALIDATION_COLUMNS
    assert validation_passed(state["validation_report"])
    assert not state["transfer_table"].empty


def test_applying_correction_moves_requested_well(monkeypatch):
    state = generated_state(monkeypatch)
    original_a1_label = state["plate_layout"].loc[state["plate_layout"]["well"] == "A1", "label"].iloc[0]

    result = apply_confirmed_correction(
        plate_layout=state["plate_layout"],
        well_contents=state["well_contents"],
        source_map=state["source_map"],
        spec=state["spec"],
        operations=[{"op": "MOVE_WELL", "source_well": "A1", "dest_well": "H12"}],
        command="move A1 to H12",
        edit_history=[],
    )

    moved_label = result["plate_layout"].loc[result["plate_layout"]["well"] == "H12", "label"].iloc[0]
    assert moved_label == original_a1_label
    assert result["edit_history"][0]["status"] == "applied"
    assert not result["diff"].empty


def test_correction_preserves_unmentioned_wells(monkeypatch):
    state = generated_state(monkeypatch)
    before_a2 = state["plate_layout"].loc[state["plate_layout"]["well"] == "A2"].iloc[0].to_dict()

    result = apply_confirmed_correction(
        plate_layout=state["plate_layout"],
        well_contents=state["well_contents"],
        source_map=state["source_map"],
        spec=state["spec"],
        operations=[{"op": "MOVE_WELL", "source_well": "A1", "dest_well": "H12"}],
        command="move A1 to H12",
        edit_history=[],
    )

    after_a2 = result["plate_layout"].loc[result["plate_layout"]["well"] == "A2"].iloc[0].to_dict()
    assert after_a2 == before_a2


def test_regenerating_source_maps_and_derived_tables(monkeypatch):
    state = generated_state(monkeypatch)
    derived = regenerate_derived_tables(
        state["plate_layout"],
        state["well_contents"],
        state["spec"],
    )

    assert not derived["source_map"].empty
    assert list(derived["source_map"].columns) == SOURCE_MAP_COLUMNS
    assert not derived["summary_matrix"].empty

    validated = build_validated_state(state["plate_layout"], state["well_contents"], state["spec"])
    assert list(validated["transfer_table"].columns) == TRANSFER_COLUMNS
    assert not validated["transfer_table"].empty


def test_validation_output(monkeypatch):
    state = generated_state(monkeypatch)
    result = run_validation(
        state["plate_layout"],
        state["well_contents"],
        state["source_map"],
        state["spec"],
    )

    report = result["validation_report"]
    assert isinstance(report, pd.DataFrame)
    assert list(report.columns) == VALIDATION_COLUMNS
    assert {"PASS"} <= set(report["status"])
    assert result["passed"] is True


def test_invalid_llm_source_names_are_assigned_to_plate_wells():
    payload = {
        "plate_layout": [
            {"well": "A1", "label": "Ld_Mi_Cs3", "sample": "", "condition": "", "replicate": 1}
        ],
        "well_contents": [
            {"well": "A1", "label": "Ld_Mi_Cs3", "liquid_name": "Ld", "liquid_role": "sample", "volume_ul": 5, "source_labware": "Sample_plate", "source_well": "Ld"},
            {"well": "A1", "label": "Ld_Mi_Cs3", "liquid_name": "Mi", "liquid_role": "sample", "volume_ul": 5, "source_labware": "Sample_plate", "source_well": "Mi"},
            {"well": "A1", "label": "Ld_Mi_Cs3", "liquid_name": "Cs3", "liquid_role": "sample", "volume_ul": 10, "source_labware": "Sample_plate", "source_well": "Cs3"},
        ],
        "sources": {
            "Ld": {"liquid_name": "Ld", "liquid_role": "sample", "source_labware": "Sample_plate", "source_well": "Ld"},
            "Mi": {"liquid_name": "Mi", "liquid_role": "sample", "source_labware": "Sample_plate", "source_well": "Mi"},
            "Cs3": {"liquid_name": "Cs3", "liquid_role": "sample", "source_labware": "Sample_plate", "source_well": "Cs3"},
        },
    }

    spec = _normalize_llm_layout_payload("combine Ld, Mi, and Cs3", payload)

    assert spec["well_contents"]["source_well"].tolist() == ["A1", "A2", "A3"]
    assert [source["source_well"] for source in spec["sources"].values()] == ["A1", "A2", "A3"]


def test_validation_rejects_invalid_source_well(monkeypatch):
    state = generated_state(monkeypatch)
    invalid_contents = state["well_contents"].copy()
    invalid_contents.loc[invalid_contents.index[0], "source_well"] = "Ld"

    result = run_validation(
        state["plate_layout"],
        invalid_contents,
        state["source_map"],
        state["spec"],
    )

    source_check = result["validation_report"].loc[
        result["validation_report"]["check_name"] == "valid_source_well_addresses"
    ].iloc[0]
    assert source_check["status"] == "FAIL"
    assert result["passed"] is False


def test_manual_plate_edit_updates_layout_contents_and_history(monkeypatch):
    state = generated_state(monkeypatch)

    edited = manually_edit_plate_well(
        plate_layout=state["plate_layout"],
        well_contents=state["well_contents"],
        spec=state["spec"],
        original_well="A1",
        well="H12",
        label="Manual_Label",
        sample="Manual_Sample",
        condition="Manual_Condition",
        replicate=2,
        assay_type="Manual_Assay",
        edit_history=[],
    )

    edited_row = edited["plate_layout"].loc[edited["plate_layout"]["well"] == "H12"].iloc[0]
    assert edited_row["label"] == "Manual_Label"
    assert edited_row["sample"] == "Manual_Sample"
    assert edited_row["replicate"] == 2
    assert "A1" not in set(edited["plate_layout"]["well"])
    assert set(edited["well_contents"].loc[edited["well_contents"]["well"] == "H12", "label"]) == {"Manual_Label"}
    assert edited["edit_history"][0]["operations"][0]["op"] == "MANUAL_EDIT_WELL"
    assert not edited["transfer_table"].empty


def test_manual_source_edit_updates_volume_location_and_transfers(monkeypatch):
    state = generated_state(monkeypatch)
    enzyme = state["source_map"].loc[state["source_map"]["liquid_name"] == "Enzyme"].iloc[0]

    edited = manually_edit_source(
        plate_layout=state["plate_layout"],
        well_contents=state["well_contents"],
        source_map=state["source_map"],
        spec=state["spec"],
        liquid_name="Enzyme",
        liquid_role="enzyme",
        original_source_labware=enzyme["source_labware"],
        original_source_well=enzyme["source_well"],
        source_labware="SourcePlate_2",
        source_well="C6",
        available_volume_ul=750.0,
        edit_history=[],
    )

    updated_source = edited["source_map"].loc[edited["source_map"]["liquid_name"] == "Enzyme"].iloc[0]
    assert updated_source["source_labware"] == "SourcePlate_2"
    assert updated_source["source_well"] == "C6"
    assert updated_source["available_volume_ul"] == 750.0
    enzyme_transfers = edited["transfer_table"].loc[edited["transfer_table"]["liquid_name"] == "Enzyme"]
    assert set(enzyme_transfers["source_labware"]) == {"SourcePlate_2"}
    assert set(enzyme_transfers["source_well"]) == {"C6"}
    assert edited["edit_history"][0]["operations"][0]["op"] == "MANUAL_EDIT_SOURCE"


def test_manual_volume_edits_update_targets_sources_and_transfers(monkeypatch):
    state = generated_state(monkeypatch)
    a1_contents = state["well_contents"].loc[state["well_contents"]["well"] == "A1"]
    enzyme = a1_contents.loc[a1_contents["liquid_name"] == "Enzyme"].iloc[0]
    water = a1_contents.loc[a1_contents["liquid_name"] == "Water"].iloc[0]

    updates = []
    for row, volume in [(enzyme, 8.0), (water, 6.0)]:
        updates.append(
            {
                "well": row["well"],
                "liquid_name": row["liquid_name"],
                "liquid_role": row["liquid_role"],
                "source_labware": row["source_labware"],
                "source_well": row["source_well"],
                "volume_ul": volume,
            }
        )

    edited = manually_edit_volumes(
        plate_layout=state["plate_layout"],
        well_contents=state["well_contents"],
        source_map=state["source_map"],
        spec=state["spec"],
        updates=updates,
        edit_history=[],
    )

    edited_a1 = edited["well_contents"].loc[edited["well_contents"]["well"] == "A1"]
    assert edited_a1.loc[edited_a1["liquid_name"] == "Enzyme", "volume_ul"].iloc[0] == 8.0
    assert edited_a1.loc[edited_a1["liquid_name"] == "Water", "volume_ul"].iloc[0] == 6.0
    assert edited["passed"] is True
    assert edited["source_map"].loc[edited["source_map"]["liquid_name"] == "Enzyme", "required_volume_ul"].iloc[0] == 323.0
    transfer = edited["transfer_table"].loc[
        (edited["transfer_table"]["destination_well"] == "A1")
        & (edited["transfer_table"]["liquid_name"] == "Enzyme")
    ].iloc[0]
    assert transfer["volume_ul"] == 8.0
