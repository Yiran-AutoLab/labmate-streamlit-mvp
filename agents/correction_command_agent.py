from __future__ import annotations

import re
from typing import Any

from agents.llm_client import chat_json


def parse_correction_command(
    command: str,
    *,
    provider: str,
    model: str,
    api_key: str,
) -> list[dict[str, Any]]:
    payload = chat_json(
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt=_correction_system_prompt(),
        user_prompt=command,
    )
    operations = payload.get("operations", payload if isinstance(payload, list) else [])
    if not isinstance(operations, list):
        raise ValueError("LLM correction response must contain an operations list.")
    return operations


def mock_parse_correction_command(command: str) -> list[dict[str, Any]]:
    """Mock LLM: parse natural language correction commands into layout operations."""

    text = command.strip()
    if not text:
        return []
    operations: list[dict[str, Any]] = []

    for source, dest in re.findall(r"(?:move|把|将)\s*([A-Ha-h]\d{1,2})\s*(?:to|到|移到)\s*([A-Ha-h]\d{1,2})", text):
        operations.append({"op": "MOVE_WELL", "source_well": source.upper(), "dest_well": dest.upper()})

    swap_match = re.search(r"(?:swap|交换)\s*([A-Ha-h]\d{1,2})\s*(?:and|和|与)\s*([A-Ha-h]\d{1,2})", text)
    if swap_match:
        operations.append({"op": "SWAP_WELLS", "well_a": swap_match.group(1).upper(), "well_b": swap_match.group(2).upper()})

    avoid_match = re.search(r"(?:avoid|不要用|避开)\s*([A-Ha-h0-9,\s/、]+)", text)
    if avoid_match:
        wells = _extract_wells(avoid_match.group(1))
        operations.append({"op": "AVOID_WELLS", "wells": wells})

    volume_match = re.search(
        r"(?:change|set|把|将)\s*([\w_ -]+?)\s*(?:volume|体积)?\s*(?:to|改成|设为|为)\s*(\d+(?:\.\d+)?)\s*u?L",
        text,
        flags=re.IGNORECASE,
    )
    if volume_match:
        operations.append(
            {
                "op": "CHANGE_VOLUME",
                "liquid_name": volume_match.group(1).strip(),
                "volume_ul": float(volume_match.group(2)),
            }
        )

    source_match = re.search(
        r"(?:change|set|把|将)\s*([\w_ -]+?)\s*(?:source|来源)?\s*(?:to|改成|设为|在)\s*([\w_]+)\s+([A-Ha-h]\d{1,2})",
        text,
        flags=re.IGNORECASE,
    )
    if source_match:
        operations.append(
            {
                "op": "CHANGE_SOURCE",
                "liquid_name": source_match.group(1).strip(),
                "source_labware": source_match.group(2),
                "source_well": source_match.group(3).upper(),
            }
        )

    if re.search(r"fill.*column|按列|column", text, flags=re.IGNORECASE):
        operations.append({"op": "FILL_BY_COLUMN"})
    if re.search(r"fill.*row|按行|row", text, flags=re.IGNORECASE):
        operations.append({"op": "FILL_BY_ROW"})
    if re.search(r"group.*condition|按.*primer|按.*condition", text, flags=re.IGNORECASE):
        operations.append({"op": "GROUP_BY", "field": "condition"})
    if re.search(r"group.*sample|按.*template|按.*sample", text, flags=re.IGNORECASE):
        operations.append({"op": "GROUP_BY", "field": "sample"})

    add_rep_match = re.search(r"(?:add|增加)\s*(\d+)?\s*(?:replicate|重复)", text, flags=re.IGNORECASE)
    if add_rep_match:
        operations.append({"op": "ADD_REPLICATE", "count": int(add_rep_match.group(1) or 1)})

    remove_rep_match = re.search(r"(?:remove|删除)\s*(\d+)?\s*(?:replicate|重复)", text, flags=re.IGNORECASE)
    if remove_rep_match:
        operations.append({"op": "REMOVE_REPLICATE", "count": int(remove_rep_match.group(1) or 1)})

    if "regenerate" in text.lower() or "重新生成" in text:
        operations.append({"op": "REGENERATE_LAYOUT_WITH_CONSTRAINTS", "constraint_text": text})

    return operations or [{"op": "REGENERATE_LAYOUT_WITH_CONSTRAINTS", "constraint_text": text}]


def _extract_wells(text: str) -> list[str]:
    return [well.upper() for well in re.findall(r"[A-Ha-h](?:[1-9]|1[0-2])", text)]


def _correction_system_prompt() -> str:
    return """You parse natural language plate layout correction commands into JSON operations.
Return ONLY JSON with this shape:
{"operations":[{"op":"MOVE_WELL","source_well":"A1","dest_well":"H12"}]}

Supported operations:
- MOVE_WELL: source_well, dest_well
- SWAP_WELLS: well_a, well_b
- MOVE_BY_LABEL: label, dest_well
- AVOID_WELLS: wells
- GROUP_BY: field, one of label/sample/condition/replicate/assay_type
- FILL_BY_ROW: no extra fields
- FILL_BY_COLUMN: no extra fields
- CHANGE_VOLUME: liquid_name, volume_ul
- CHANGE_SOURCE: liquid_name, source_labware, source_well
- ADD_REPLICATE: count
- REMOVE_REPLICATE: count
- REGENERATE_LAYOUT_WITH_CONSTRAINTS: constraint_text

Use valid well addresses like A1-H12. Prefer specific operations over regenerate."""
