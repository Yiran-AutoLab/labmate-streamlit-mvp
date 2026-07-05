from __future__ import annotations

from typing import Any

import pandas as pd

from models.plate_models import PLATE_LAYOUT_COLUMNS, VALID_WELLS, ensure_columns, next_well_after_avoids
from workflows.layout_generator import reorder_layout


def apply_layout_operations(
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    operations: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    before_layout = ensure_columns(plate_layout, PLATE_LAYOUT_COLUMNS).copy()
    layout = before_layout.copy()
    before_contents = well_contents.copy()
    contents = before_contents.copy()

    for operation in operations:
        op = operation.get("op")
        if op == "MOVE_WELL":
            layout, contents = _move_well(layout, contents, operation["source_well"], operation["dest_well"])
        elif op == "SWAP_WELLS":
            layout, contents = _swap_wells(layout, contents, operation["well_a"], operation["well_b"])
        elif op == "MOVE_BY_LABEL":
            matches = layout["label"].astype(str) == str(operation["label"])
            for well in layout.loc[matches, "well"].tolist():
                layout, contents = _move_well(layout, contents, well, operation["dest_well"])
        elif op == "AVOID_WELLS":
            layout, contents = _avoid_wells(layout, contents, set(operation.get("wells", [])))
        elif op == "FILL_BY_ROW":
            old_wells = layout["well"].tolist()
            layout = reorder_layout(layout, "row")
            contents = _remap_contents(contents, dict(zip(old_wells, layout["well"].tolist())))
        elif op == "FILL_BY_COLUMN":
            old_wells = layout["well"].tolist()
            layout = reorder_layout(layout, "column")
            contents = _remap_contents(contents, dict(zip(old_wells, layout["well"].tolist())))
        elif op == "GROUP_BY":
            old_wells = layout["well"].tolist()
            layout = layout.sort_values([operation.get("field", "condition"), "sample", "label"]).reset_index(drop=True)
            layout = reorder_layout(layout, "row")
            contents = _remap_contents(contents, dict(zip(old_wells, layout["well"].tolist())))
        elif op == "CHANGE_VOLUME":
            mask = _liquid_mask(contents, operation["liquid_name"])
            contents.loc[mask, "volume_ul"] = float(operation["volume_ul"])
        elif op == "CHANGE_SOURCE":
            mask = _liquid_mask(contents, operation["liquid_name"])
            contents.loc[mask, "source_labware"] = operation["source_labware"]
            contents.loc[mask, "source_well"] = operation.get("source_well", operation.get("source_position", ""))
        elif op == "CONSOLIDATE_SOURCES":
            contents = _consolidate_sources(
                contents,
                operation.get("source_labware", "SourcePlate_1"),
                operation.get("start_well", "A1"),
            )
        elif op == "ADD_REPLICATE":
            layout, contents = _add_replicate(layout, contents, int(operation.get("count", 1)))
        elif op == "REMOVE_REPLICATE":
            layout, contents = _remove_replicate(layout, contents, int(operation.get("count", 1)))
        elif op == "REGENERATE_LAYOUT_WITH_CONSTRAINTS":
            constraint_text = str(operation.get("constraint_text", ""))
            if _mentions_source_consolidation(constraint_text):
                contents = _consolidate_sources(contents, "SourcePlate_1", "A1")
            else:
                old_wells = layout["well"].tolist()
                layout = reorder_layout(layout.sort_values(["condition", "sample", "label"]), "row")
                contents = _remap_contents(contents, dict(zip(old_wells, layout["well"].tolist())))

    diff = build_combined_diff(before_layout, layout, before_contents, contents)
    return layout.reset_index(drop=True), contents.reset_index(drop=True), diff


def build_diff(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    rows = []
    before_by_label = before.set_index("label")["well"].to_dict() if not before.empty else {}
    after_by_label = after.set_index("label")["well"].to_dict() if not after.empty else {}
    for label in sorted(set(before_by_label) | set(after_by_label)):
        before_well = before_by_label.get(label, "")
        after_well = after_by_label.get(label, "")
        if before_well != after_well:
            rows.append({"label": label, "before_well": before_well, "after_well": after_well})
    return pd.DataFrame(rows)


def build_combined_diff(
    before_layout: pd.DataFrame,
    after_layout: pd.DataFrame,
    before_contents: pd.DataFrame,
    after_contents: pd.DataFrame,
) -> pd.DataFrame:
    rows = build_diff(before_layout, after_layout).to_dict("records")
    for row in rows:
        row["change_type"] = "layout"

    compare_columns = ["volume_ul", "source_labware", "source_well"]
    key_columns = ["well", "label", "liquid_name", "liquid_role"]
    before = before_contents.copy()
    after = after_contents.copy()
    for column in key_columns + compare_columns:
        if column not in before.columns:
            before[column] = ""
        if column not in after.columns:
            after[column] = ""

    before["_key"] = before[key_columns].astype(str).agg("|".join, axis=1)
    after["_key"] = after[key_columns].astype(str).agg("|".join, axis=1)
    before_by_key = before.set_index("_key", drop=False)
    after_by_key = after.set_index("_key", drop=False)

    for key in sorted(set(before_by_key.index) | set(after_by_key.index)):
        if key not in before_by_key.index:
            after_row = after_by_key.loc[key]
            rows.append(
                {
                    "change_type": "content_added",
                    "label": after_row.get("label", ""),
                    "well": after_row.get("well", ""),
                    "liquid_name": after_row.get("liquid_name", ""),
                    "before": "",
                    "after": "added",
                }
            )
            continue
        if key not in after_by_key.index:
            before_row = before_by_key.loc[key]
            rows.append(
                {
                    "change_type": "content_removed",
                    "label": before_row.get("label", ""),
                    "well": before_row.get("well", ""),
                    "liquid_name": before_row.get("liquid_name", ""),
                    "before": "present",
                    "after": "",
                }
            )
            continue

        before_row = before_by_key.loc[key]
        after_row = after_by_key.loc[key]
        for column in compare_columns:
            before_value = before_row.get(column, "")
            after_value = after_row.get(column, "")
            if str(before_value) != str(after_value):
                rows.append(
                    {
                        "change_type": column,
                        "label": after_row.get("label", ""),
                        "well": after_row.get("well", ""),
                        "liquid_name": after_row.get("liquid_name", ""),
                        "before": before_value,
                        "after": after_value,
                    }
                )

    return pd.DataFrame(rows)


def _normalize_liquid(value: object) -> str:
    return "".join(char for char in str(value).lower() if char.isalnum())


def _liquid_mask(contents: pd.DataFrame, liquid_name: object) -> pd.Series:
    wanted = _normalize_liquid(liquid_name)
    if not wanted:
        return pd.Series(False, index=contents.index)
    names = contents["liquid_name"].map(_normalize_liquid)
    roles = contents["liquid_role"].map(_normalize_liquid)
    return names.eq(wanted) | roles.eq(wanted) | names.str.contains(wanted, regex=False) | names.map(lambda value: wanted in value)


def _mentions_source_consolidation(text: str) -> bool:
    lowered = text.lower()
    has_source = "source" in lowered or "来源" in text
    has_plate = "plate" in lowered or "板" in text
    has_single = any(token in text for token in ["一个板", "同一个板", "一个板子", "同一块板", "一块板"])
    has_all = any(token in text for token in ["都", "全部", "所有", "all"])
    return has_source and has_plate and (has_single or has_all)


def _consolidate_sources(contents: pd.DataFrame, source_labware: str, start_well: str = "A1") -> pd.DataFrame:
    result = contents.copy()
    source_labware = str(source_labware or "SourcePlate_1").strip()
    start_well = str(start_well or "A1").strip().upper()
    start_index = VALID_WELLS.index(start_well) if start_well in VALID_WELLS else 0
    source_wells = VALID_WELLS[start_index:]

    unique_liquids = (
        result[["liquid_name", "liquid_role"]]
        .drop_duplicates()
        .sort_values(["liquid_role", "liquid_name"])
        .reset_index(drop=True)
    )
    if len(unique_liquids) > len(source_wells):
        raise ValueError("Not enough wells to place all source liquids on one 96-well source plate.")

    for idx, row in enumerate(unique_liquids.itertuples(index=False)):
        mask = (
            result["liquid_name"].astype(str).eq(str(row.liquid_name))
            & result["liquid_role"].astype(str).eq(str(row.liquid_role))
        )
        result.loc[mask, "source_labware"] = source_labware
        result.loc[mask, "source_well"] = source_wells[idx]
    return result


def _move_well(layout: pd.DataFrame, contents: pd.DataFrame, source_well: str, dest_well: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_well = source_well.upper()
    dest_well = dest_well.upper()
    layout = layout.copy()
    contents = contents.copy()
    if dest_well in set(layout["well"]):
        layout, contents = _swap_wells(layout, contents, source_well, dest_well)
    else:
        layout.loc[layout["well"] == source_well, "well"] = dest_well
        contents.loc[contents["well"] == source_well, "well"] = dest_well
    return layout, contents


def _swap_wells(layout: pd.DataFrame, contents: pd.DataFrame, well_a: str, well_b: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    well_a = well_a.upper()
    well_b = well_b.upper()
    layout = layout.copy()
    contents = contents.copy()
    tmp = "__TMP__"
    layout.loc[layout["well"] == well_a, "well"] = tmp
    layout.loc[layout["well"] == well_b, "well"] = well_a
    layout.loc[layout["well"] == tmp, "well"] = well_b
    contents.loc[contents["well"] == well_a, "well"] = tmp
    contents.loc[contents["well"] == well_b, "well"] = well_a
    contents.loc[contents["well"] == tmp, "well"] = well_b
    return layout, contents


def _avoid_wells(layout: pd.DataFrame, contents: pd.DataFrame, avoided: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    used = set(layout["well"])
    for well in list(layout["well"]):
        if well in avoided:
            new_well = next_well_after_avoids(used, avoided)
            used.remove(well)
            used.add(new_well)
            layout, contents = _move_well(layout, contents, well, new_well)
    return layout, contents


def _remap_contents(contents: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    result = contents.copy()
    result["well"] = result["well"].map(lambda well: mapping.get(well, well))
    return result


def _add_replicate(layout: pd.DataFrame, contents: pd.DataFrame, count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    layout = layout.copy()
    contents = contents.copy()
    used = set(layout["well"])
    new_layout_rows = []
    new_content_rows = []
    for _ in range(count):
        for source_row in layout.itertuples(index=False):
            new_well = next_well_after_avoids(used, set())
            used.add(new_well)
            source_dict = source_row._asdict()
            old_well = source_dict["well"]
            source_dict["well"] = new_well
            source_dict["replicate"] = int(source_dict.get("replicate", 1)) + 1
            source_dict["label"] = f"{source_dict['sample']}_{source_dict['condition']}_rep{source_dict['replicate']}"
            new_layout_rows.append(source_dict)
            for content in contents[contents["well"] == old_well].to_dict("records"):
                content["well"] = new_well
                content["label"] = source_dict["label"]
                new_content_rows.append(content)
    return pd.concat([layout, pd.DataFrame(new_layout_rows)], ignore_index=True), pd.concat([contents, pd.DataFrame(new_content_rows)], ignore_index=True)


def _remove_replicate(layout: pd.DataFrame, contents: pd.DataFrame, count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    layout = layout.copy()
    for _ in range(count):
        max_rep = pd.to_numeric(layout["replicate"], errors="coerce").max()
        if max_rep <= 1:
            break
        remove_wells = set(layout.loc[pd.to_numeric(layout["replicate"], errors="coerce") == max_rep, "well"])
        layout = layout[~layout["well"].isin(remove_wells)]
        contents = contents[~contents["well"].isin(remove_wells)]
    return layout, contents
