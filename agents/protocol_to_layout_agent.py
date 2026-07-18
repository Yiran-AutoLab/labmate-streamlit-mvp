from __future__ import annotations

import re
from typing import Any

import pandas as pd

from agents.llm_client import chat_json
from models.plate_models import PLATE_LAYOUT_COLUMNS, VALID_WELLS, WELL_CONTENT_COLUMNS, PlateLayoutRow, dataclass_rows_to_df
from workflows.source_allocator import assign_invalid_source_wells


def protocol_to_layout(
    protocol: str,
    *,
    provider: str,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """Use a real LLM to convert a protocol into normalized layout/spec tables."""

    payload = chat_json(
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt=_protocol_system_prompt(),
        user_prompt=protocol,
    )
    return _normalize_llm_layout_payload(protocol, payload)


def mock_protocol_to_layout(protocol: str) -> dict[str, Any]:
    """Mock LLM: convert a PCR-like protocol into a draft layout and liquid spec."""

    template_count = _range_count(protocol, r"T(\d+)-T(\d+)", default=12)
    primer_count = _range_count(protocol, r"P(\d+)-P(\d+)", default=4)
    templates = [f"T{i}" for i in range(1, template_count + 1)]
    primers = [f"P{i}" for i in range(1, primer_count + 1)]

    template_to_primers: dict[str, list[str]] = {}
    for template in templates:
        template_number = int(template[1:])
        if template_number <= 6 and len(primers) >= 2:
            template_to_primers[template] = primers[:2]
        else:
            template_to_primers[template] = primers

    volumes = {
        "enzyme": _volume_after("enzyme", protocol, 9),
        "water": _volume_after("water", protocol, 5),
        "forward_primer": _volume_after("forward primer", protocol, 1),
        "reverse_primer": _volume_after("reverse primer", protocol, 1),
        "template": _volume_after("template", protocol, 4),
    }
    expected_total = sum(volumes.values())

    sources = _default_sources(template_count, primer_count)
    rows: list[PlateLayoutRow] = []
    well_index = 0
    for template in templates:
        for primer in template_to_primers[template]:
            rows.append(
                PlateLayoutRow(
                    well=VALID_WELLS[well_index],
                    label=f"{template}_{primer}",
                    sample=template,
                    condition=primer,
                    replicate=1,
                    assay_type="PCR",
                )
            )
            well_index += 1

    layout_df = dataclass_rows_to_df(rows)
    if layout_df.empty:
        layout_df = pd.DataFrame(columns=PLATE_LAYOUT_COLUMNS)

    return {
        "protocol_text": protocol,
        "destination_labware": "PCR_plate_96",
        "expected_total_volume_ul": expected_total,
        "plate_layout": layout_df,
        "template_to_primers": template_to_primers,
        "volumes": volumes,
        "sources": sources,
        "assumptions": [
            "Mock LLM filled reactions row-wise from A1.",
            "One replicate per template-primer reaction unless corrected by user.",
            "No NTC wells were added because the example protocol did not request them.",
        ],
    }


def _range_count(text: str, pattern: str, default: int) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return default
    return int(match.group(2))


def _volume_after(name: str, text: str, default: float) -> float:
    pattern = rf"{re.escape(name)}\s+(\d+(?:\.\d+)?)\s*u?L"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else float(default)


def _default_sources(template_count: int, primer_count: int) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {
        "enzyme": {
            "liquid_name": "Enzyme",
            "liquid_role": "enzyme",
            "source_labware": "Reservoir_1",
            "source_well": "A1",
            "available_volume_ul": 10000.0,
        },
        "water": {
            "liquid_name": "Water",
            "liquid_role": "water",
            "source_labware": "Reservoir_1",
            "source_well": "A2",
            "available_volume_ul": 10000.0,
        },
    }
    primer_source_rows = ["A", "B", "C", "D", "E", "F", "G", "H"]
    for idx in range(1, primer_count + 1):
        row = primer_source_rows[idx - 1]
        sources[f"P{idx}_forward"] = {
            "liquid_name": f"P{idx}_Forward",
            "liquid_role": "forward_primer",
            "source_labware": "Reservoir_1",
            "source_well": f"{row}3",
            "available_volume_ul": 500.0,
        }
        sources[f"P{idx}_reverse"] = {
            "liquid_name": f"P{idx}_Reverse",
            "liquid_role": "reverse_primer",
            "source_labware": "Reservoir_1",
            "source_well": f"{row}4",
            "available_volume_ul": 500.0,
        }
    for idx in range(1, template_count + 1):
        sources[f"T{idx}"] = {
            "liquid_name": f"T{idx}",
            "liquid_role": "template",
            "source_labware": "Sample_plate",
            "source_well": f"A{idx}",
            "available_volume_ul": 50.0,
        }
    return sources


def _protocol_system_prompt() -> str:
    return """You are a lab automation plate layout planner.
Return ONLY JSON. Do not include markdown.

Task:
Given a natural language liquid-handling protocol, produce a draft 96-well destination plate layout and a complete well contents table.
This is a GENERAL liquid-handling layout task. Do not assume PCR unless the protocol is PCR.

Required JSON schema:
{
  "workflow_name": "PCR setup or OD600 setup or custom workflow name",
  "destination_labware": "Plate_96",
  "expected_total_volume_ul": 20,
  "plate_layout": [
    {"well":"A1","label":"T1_P1","sample":"T1","condition":"P1","replicate":1,"assay_type":"PCR"}
  ],
  "well_contents": [
    {"well":"A1","label":"T1_P1","liquid_name":"Enzyme","liquid_role":"enzyme","volume_ul":9,"source_labware":"Reservoir_1","source_well":"A1"}
  ],
  "sources": {
    "enzyme": {"liquid_name":"Enzyme","liquid_role":"enzyme","source_labware":"Reservoir_1","source_well":"A1","available_volume_ul":10000},
    "T1": {"liquid_name":"T1","liquid_role":"sample","source_labware":"Sample_plate","source_well":"A1","available_volume_ul":50}
  },
  "assumptions": ["..."]
}

Rules:
- Return compact JSON. Do not pretty-print large arrays.
- The plate_layout is the destination layout. Use valid 96-well addresses A1-H12.
- label should uniquely describe the destination well content or reaction.
- sample, condition, replicate, and assay_type are generic metadata columns. Use empty string if unknown.
- Put destination reactions row-wise unless the protocol says otherwise.
- The well_contents table is authoritative. Include every liquid to add to every destination well.
- Use liquid_role generically, e.g. enzyme, water, primer, template, medium, sample, compound, buffer, dye, control.
- Use source_labware and source_well for every liquid whenever the protocol states or implies them.
- The sources object may be minimal because source usage can be inferred from well_contents.
- If a source available volume is not stated, use a conservative placeholder.
- If available source volume is not stated, use a conservative placeholder."""


def _normalize_llm_layout_payload(protocol: str, payload: dict[str, Any]) -> dict[str, Any]:
    layout_rows = payload.get("plate_layout", [])
    layout_df = pd.DataFrame(layout_rows)
    if layout_df.empty:
        raise ValueError("LLM returned an empty plate_layout.")
    for column in PLATE_LAYOUT_COLUMNS:
        if column not in layout_df.columns:
            layout_df[column] = "" if column != "replicate" else 1
    layout_df = layout_df[PLATE_LAYOUT_COLUMNS].copy()
    layout_df["well"] = layout_df["well"].astype(str).str.upper()
    layout_df["replicate"] = pd.to_numeric(layout_df["replicate"], errors="coerce").fillna(1).astype(int)

    volumes = _normalize_volumes(payload, required=False)
    well_contents = _normalize_well_contents(payload, layout_df)

    sources = _normalize_sources(payload.get("sources", {}), layout_df, well_contents)
    if not isinstance(sources, dict) or not sources:
        raise ValueError("LLM payload missing sources and well_contents source columns.")
    for key, source in sources.items():
        source.setdefault("liquid_name", key)
        source.setdefault("liquid_role", key)
        source.setdefault("source_labware", "")
        source.setdefault("source_well", "")
        source.setdefault("available_volume_ul", 0.0)
        source["source_well"] = str(source["source_well"]).upper()
        source["available_volume_ul"] = float(source.get("available_volume_ul") or 0)

    well_contents, sources = assign_invalid_source_wells(well_contents, sources)

    return {
        "protocol_text": protocol,
        "workflow_name": payload.get("workflow_name", payload.get("experiment_type", "Custom liquid handling workflow")),
        "destination_labware": payload.get("destination_labware", "Plate_96"),
        "expected_total_volume_ul": _optional_float(payload.get("expected_total_volume_ul")),
        "plate_layout": layout_df,
        "well_contents": well_contents,
        "volumes": volumes,
        "sources": sources,
        "assumptions": payload.get("assumptions", []),
    }


def _normalize_well_contents(payload: dict[str, Any], layout_df: pd.DataFrame) -> pd.DataFrame:
    rows = payload.get("well_contents") or payload.get("contents") or payload.get("well_content_rows") or []
    contents_df = pd.DataFrame(rows)
    if contents_df.empty:
        return pd.DataFrame(columns=WELL_CONTENT_COLUMNS)
    for column in WELL_CONTENT_COLUMNS:
        if column not in contents_df.columns:
            contents_df[column] = ""
    contents_df = contents_df[WELL_CONTENT_COLUMNS].copy()
    contents_df["well"] = contents_df["well"].astype(str).str.upper()
    contents_df["volume_ul"] = pd.to_numeric(contents_df["volume_ul"], errors="coerce")
    label_lookup = layout_df.set_index("well")["label"].to_dict()
    contents_df["label"] = contents_df.apply(
        lambda row: row["label"] or label_lookup.get(row["well"], ""),
        axis=1,
    )
    return contents_df


def _normalize_volumes(payload: dict[str, Any], required: bool = False) -> dict[str, float]:
    raw = payload.get("volumes") or payload.get("reaction_volumes") or payload.get("liquid_volumes") or {}
    if isinstance(raw, list):
        raw = {
            item.get("liquid_role") or item.get("liquid_name") or item.get("name"): item.get("volume_ul") or item.get("volume")
            for item in raw
            if isinstance(item, dict)
        }
    normalized = {_canonical_volume_key(key): value for key, value in raw.items()}
    required_keys = ["enzyme", "water", "forward_primer", "reverse_primer", "template"]
    missing = [key for key in required_keys if key not in normalized]
    if required and missing:
        known = ", ".join(str(key) for key in raw.keys())
        raise ValueError(f"LLM payload missing volumes {missing}. Returned volume keys: {known}")
    return {key: float(value) for key, value in normalized.items() if value not in [None, ""]}


def _optional_float(value: Any) -> float | None:
    if value in [None, ""]:
        return None
    return float(value)


def _canonical_volume_key(key: Any) -> str:
    text = str(key).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "enzyme": "enzyme",
        "polymerase": "enzyme",
        "master_mix": "enzyme",
        "mix": "enzyme",
        "enzyme_volume_ul": "enzyme",
        "water": "water",
        "ddh2o": "water",
        "h2o": "water",
        "water_volume_ul": "water",
        "forward": "forward_primer",
        "forward_primer": "forward_primer",
        "forward_primer_volume_ul": "forward_primer",
        "fwd_primer": "forward_primer",
        "reverse": "reverse_primer",
        "reverse_primer": "reverse_primer",
        "reverse_primer_volume_ul": "reverse_primer",
        "rev_primer": "reverse_primer",
        "template": "template",
        "sample": "template",
        "template_volume_ul": "template",
    }
    return aliases.get(text, text)


def _normalize_sources(raw_sources: Any, layout_df: pd.DataFrame, well_contents: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if isinstance(raw_sources, list):
        items = raw_sources
    elif isinstance(raw_sources, dict):
        items = []
        for key, value in raw_sources.items():
            if isinstance(value, dict):
                source = dict(value)
                source.setdefault("source_key", key)
                items.append(source)
    else:
        items = []

    normalized: dict[str, dict[str, Any]] = {}
    for item in items:
        key = _canonical_source_key(item.get("source_key") or item.get("liquid_name") or item.get("name") or item.get("liquid_role"))
        source = {
            "liquid_name": item.get("liquid_name") or item.get("name") or key,
            "liquid_role": _canonical_volume_key(item.get("liquid_role") or key),
            "source_labware": item.get("source_labware") or item.get("labware") or "",
            "source_well": str(item.get("source_well") or item.get("source_position") or item.get("well") or "").upper(),
            "available_volume_ul": float(item.get("available_volume_ul") or item.get("available_volume") or 0),
        }
        normalized[key] = source

    samples = sorted({value for value in layout_df["sample"].astype(str).str.strip() if value})
    conditions = sorted({value for value in layout_df["condition"].astype(str).str.strip() if value})
    for sample in samples:
        if sample.upper() != "NTC" and sample not in normalized:
            candidate = _find_source_by_name(normalized, sample)
            if candidate:
                normalized[sample] = candidate
    for condition in conditions:
        forward_key = f"{condition}_forward"
        reverse_key = f"{condition}_reverse"
        if forward_key not in normalized:
            candidate = _find_source_by_name(normalized, f"{condition}_forward") or _find_source_by_name(normalized, f"{condition}_f")
            if candidate:
                normalized[forward_key] = candidate
        if reverse_key not in normalized:
            candidate = _find_source_by_name(normalized, f"{condition}_reverse") or _find_source_by_name(normalized, f"{condition}_r")
            if candidate:
                normalized[reverse_key] = candidate

    if not well_contents.empty:
        for row in well_contents.itertuples(index=False):
            if not row.source_labware or not row.source_well:
                continue
            key = _canonical_source_key(row.liquid_name or row.liquid_role)
            normalized.setdefault(
                key,
                {
                    "liquid_name": row.liquid_name,
                    "liquid_role": row.liquid_role,
                    "source_labware": row.source_labware,
                    "source_well": str(row.source_well).upper(),
                    "available_volume_ul": 0.0,
                },
            )

    return normalized


def _canonical_source_key(key: Any) -> str:
    text = str(key).strip().replace(" ", "_").replace("-", "_")
    lower = text.lower()
    if lower in {"enzyme", "polymerase", "master_mix", "mix"}:
        return "enzyme"
    if lower in {"water", "ddh2o", "h2o"}:
        return "water"
    match = re.fullmatch(r"(p\d+)_(forward|fwd|f)", lower)
    if match:
        return f"{match.group(1).upper()}_forward"
    match = re.fullmatch(r"(p\d+)_(reverse|rev|r)", lower)
    if match:
        return f"{match.group(1).upper()}_reverse"
    match = re.fullmatch(r"(t\d+)", lower)
    if match:
        return match.group(1).upper()
    return text


def _find_source_by_name(sources: dict[str, dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    wanted_key = _canonical_source_key(wanted).lower()
    for key, source in sources.items():
        candidates = [
            key,
            source.get("liquid_name", ""),
            source.get("source_key", ""),
            source.get("liquid_role", ""),
        ]
        if any(_canonical_source_key(candidate).lower() == wanted_key for candidate in candidates):
            return dict(source)
    return None
