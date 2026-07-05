from __future__ import annotations

from pathlib import Path

from agents.correction_command_agent import mock_parse_correction_command
from agents.protocol_to_layout_agent import mock_protocol_to_layout
from outputs.excel_writer import build_excel_workbook
from validators.layout_validator import validate_layout
from validators.source_validator import validate_sources, validation_results_to_df
from validators.volume_validator import validate_volumes
from workflows.layout_editor import apply_layout_operations
from workflows.layout_generator import summary_matrix
from workflows.transfer_compiler import compile_hamilton_transfers
from workflows.well_contents_generator import generate_source_map, generate_well_contents


def main() -> None:
    protocol = Path("examples/pcr_example.txt").read_text(encoding="utf-8")
    spec = mock_protocol_to_layout(protocol)
    plate_layout = spec["plate_layout"]
    well_contents = generate_well_contents(plate_layout, spec)
    source_map = generate_source_map(well_contents, spec)
    results = []
    results.extend(validate_layout(plate_layout))
    results.extend(validate_volumes(well_contents, spec["expected_total_volume_ul"]))
    results.extend(validate_sources(well_contents, source_map))
    validation_report = validation_results_to_df(results)

    assert len(plate_layout) == 36
    assert len(well_contents) == 180
    assert set(validation_report["status"]) == {"PASS"}

    operations = mock_parse_correction_command("move A1 to H12")
    edited_layout, edited_contents, diff = apply_layout_operations(plate_layout, well_contents, operations)
    assert "H12" in set(edited_layout["well"])
    assert not diff.empty

    transfer_table = compile_hamilton_transfers(edited_contents, spec["destination_labware"])
    assert len(transfer_table) == len(edited_contents)
    workbook = build_excel_workbook(
        edited_layout,
        edited_contents,
        generate_source_map(edited_contents, spec),
        transfer_table,
        validation_report,
        summary_matrix(edited_layout),
    )
    assert len(workbook) > 1000
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
