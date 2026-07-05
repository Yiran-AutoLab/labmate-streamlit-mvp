"""Validation checks for the OD600 96-well MVP."""

from __future__ import annotations

import re

import pandas as pd


VALID_WELL_RE = re.compile(r"^[A-H](?:[1-9]|1[0-2])$")
MAX_96_WELL_VOLUME_UL = 300


def validate_design(design: dict, plate_map: pd.DataFrame | None = None, worklist: pd.DataFrame | None = None) -> pd.DataFrame:
    checks = []

    _add_check(
        checks,
        "experiment_type",
        design.get("experiment_type") == "OD600_96_WELL",
        "Only OD600_96_WELL is supported in the MVP.",
    )
    _add_check(checks, "dest_labware", bool(design.get("dest_labware")), "Destination labware is required.")
    _add_check(checks, "groups_present", bool(design.get("groups")), "At least one Control/Treatment group is required.")

    total_requested = len(design.get("blank_wells", [])) + sum(int(group.get("replicates", 0)) for group in design.get("groups", []))
    _add_check(checks, "plate_capacity", total_requested <= 96, f"Requested {total_requested} wells; 96-well plate capacity is 96.")

    blank_wells = design.get("blank_wells", [])
    _add_check(checks, "blank_well_format", all(_valid_well(well) for well in blank_wells), "Blank wells must be A1-H12.")
    _add_check(checks, "blank_well_unique", len(blank_wells) == len(set(blank_wells)), "Blank wells must be unique.")

    medium = design.get("medium", {})
    sample = design.get("sample", {})
    _add_check(checks, "medium_volume", float(medium.get("volume_uL", 0)) > 0, "Medium volume must be positive.")
    _add_check(checks, "sample_volume", float(sample.get("volume_uL", 0)) >= 0, "Sample volume cannot be negative.")
    _add_check(checks, "medium_source", bool(medium.get("source_labware")) and _valid_well(medium.get("source_position", "")), "Medium source labware and A1-H12 source position are required.")

    for group in design.get("groups", []):
        _add_check(checks, f"group_{group.get('name')}_replicates", int(group.get("replicates", 0)) > 0, "Each group needs at least one replicate.")
        components = group.get("components") or []
        if components:
            source_ok = all(
                bool(component.get("source_labware")) and _valid_well(component.get("source_position", ""))
                for component in components
            )
        else:
            source_ok = bool(group.get("source_labware")) and _valid_well(group.get("source_position", ""))
        _add_check(
            checks,
            f"group_{group.get('name')}_source",
            source_ok,
            "Each group needs source labware and A1-H12 source position.",
        )

    if plate_map is not None and not plate_map.empty:
        _add_check(checks, "plate_map_wells_unique", plate_map["Well"].is_unique, "Destination wells must be unique.")
        _add_check(checks, "plate_map_well_format", plate_map["Well"].map(_valid_well).all(), "Destination wells must be A1-H12.")
        _add_check(
            checks,
            "well_volume_limit",
            (plate_map["TotalVolume_uL"].astype(float) <= MAX_96_WELL_VOLUME_UL).all(),
            f"Total volume should not exceed {MAX_96_WELL_VOLUME_UL} uL per well.",
        )

    if worklist is not None and not worklist.empty:
        _add_check(checks, "worklist_steps_unique", worklist["Step"].is_unique, "Worklist Step values must be unique.")
        _add_check(checks, "worklist_positive_volume", (worklist["Volume_uL"].astype(float) > 0).all(), "Every worklist transfer must have positive volume.")

    return pd.DataFrame(checks, columns=["Severity", "Check", "Status", "Message"])


def has_errors(validation_report: pd.DataFrame) -> bool:
    return ((validation_report["Severity"] == "ERROR") & (validation_report["Status"] == "FAIL")).any()


def _add_check(checks: list[dict], name: str, passed: bool, message: str, severity: str = "ERROR") -> None:
    checks.append(
        {
            "Severity": severity,
            "Check": name,
            "Status": "PASS" if passed else "FAIL",
            "Message": "OK" if passed else message,
        }
    )


def _valid_well(value: str) -> bool:
    return bool(VALID_WELL_RE.fullmatch(str(value or "").strip().upper()))
