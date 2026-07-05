from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

from labmate.parser import _extract_gemini_text, _extract_response_text, _strip_json_fences


__all__ = ["make_agent_plan", "make_llm_agent_plan", "normalize_pcr_slots"]


PCR_KEYWORDS = [
    "PCR",
    "pcr",
    "qPCR",
    "template",
    "primer",
    "引物",
    "模板",
    "NTC",
]

REQUIRED_PCR_FIELDS = {
    "template_count",
    "primer_pair_count",
    "enzyme_volume_uL",
    "water_volume_uL",
    "forward_primer_volume_uL",
    "reverse_primer_volume_uL",
    "template_volume_uL",
}

DEFAULTABLE_PCR_FIELDS = {
    "ntc_replicates",
    "common_mix_source_labware",
    "common_mix_source_position",
    "ntc_water_source_labware",
    "ntc_water_source_position",
    "destination_labware",
    "destination_start_well",
    "premix_first",
    "overage_percent",
    "destination_layout",
    "template_source_labware",
    "template_start_position",
    "premix_labware",
    "premix_position",
    "common_mix_volume_uL",
    "reaction_volume_uL",
}


def make_agent_plan(description: str) -> dict[str, Any]:
    stripped = description.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and parsed.get("experiment_type") == "PCR_SETUP":
                return _pcr_plan()
            if isinstance(parsed, dict) and parsed.get("experiment_type") == "OD600_96_WELL":
                return _od600_plan()
        except json.JSONDecodeError:
            pass

    if any(keyword in description for keyword in PCR_KEYWORDS):
        return _pcr_plan()
    return _od600_plan()


def make_llm_agent_plan(
    description: str,
    provider: str = "rules",
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    if provider in {"rules", "local", "local rules"}:
        return make_agent_plan(description)

    prompt = _llm_planner_prompt(description)
    if provider == "openai":
        plan = _call_openai_planner(prompt, api_key=api_key, model=model)
    elif provider == "gemini":
        plan = _call_gemini_planner(prompt, api_key=api_key, model=model)
    elif provider == "groq":
        plan = _call_openai_compatible_planner(
            prompt,
            api_key=api_key or os.environ.get("GROQ_API_KEY"),
            model=model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            url="https://api.groq.com/openai/v1/chat/completions",
            provider_name="Groq",
        )
    elif provider == "openrouter":
        plan = _call_openai_compatible_planner(
            prompt,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
            model=model or os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
            url="https://openrouter.ai/api/v1/chat/completions",
            provider_name="OpenRouter",
            extra_headers={"HTTP-Referer": "http://localhost:8501", "X-Title": "LabMate"},
        )
    else:
        return make_agent_plan(description)

    return _normalize_agent_plan(plan)


def normalize_pcr_slots(
    slots: dict,
    raw_description: str = "",
    llm_missing_fields: list | None = None,
) -> tuple[dict, list[str], list[str]]:
    normalized_slots = dict(slots or {})
    assumptions: list[str] = []

    cleaned_llm_missing = []
    for field in llm_missing_fields or []:
        if not isinstance(field, str):
            continue
        cleaned = field.strip()
        if not cleaned or cleaned.endswith("_") or cleaned in DEFAULTABLE_PCR_FIELDS:
            continue
        cleaned_llm_missing.append(cleaned)

    has_ntc = _text_mentions_any(raw_description, ["NTC", "negative control", "阴性对照", "无模板对照"])
    if _is_missing(normalized_slots.get("ntc_replicates")):
        normalized_slots["ntc_replicates"] = 0 if not has_ntc else 1
        assumptions.append(
            "NTC was not specified, using 0."
            if not has_ntc
            else "NTC was mentioned without replicate count, using 1."
        )

    _set_default(normalized_slots, "destination_labware", "PCR_plate_96", assumptions)
    _set_default(normalized_slots, "destination_start_well", "A1", assumptions)
    _set_default(normalized_slots, "destination_layout", "auto", assumptions)
    _set_default(normalized_slots, "overage_percent", 10.0, assumptions)
    _set_default(normalized_slots, "template_source_labware", "SampleRack_1", assumptions)
    _set_default(normalized_slots, "template_start_position", "B1", assumptions)

    premix_words = ["预混", "棰勬贩", "premix", "master mix", "common mix", "common PCR mix"]
    if _is_missing(normalized_slots.get("premix_first")):
        component_mix_ready = all(
            not _is_missing(normalized_slots.get(field))
            for field in (
                "enzyme_volume_uL",
                "water_volume_uL",
                "forward_primer_volume_uL",
                "reverse_primer_volume_uL",
            )
        )
        normalized_slots["premix_first"] = (
            _text_mentions_any(raw_description, premix_words)
            or int(float(normalized_slots.get("primer_pair_count", 1) or 1)) > 1
            or component_mix_ready
        )
        assumptions.append(
            "Using premix_first=True based on premix wording, multiple primer pairs, or explicit premix components."
            if normalized_slots["premix_first"]
            else "Premix was not specified, using premix_first=False."
        )
    else:
        normalized_slots["premix_first"] = _to_bool(normalized_slots["premix_first"])

    if _is_missing(normalized_slots.get("common_mix_volume_uL")):
        component_values = [
            normalized_slots.get("enzyme_volume_uL"),
            normalized_slots.get("water_volume_uL"),
            normalized_slots.get("forward_primer_volume_uL"),
            normalized_slots.get("reverse_primer_volume_uL"),
        ]
        if all(not _is_missing(value) for value in component_values):
            normalized_slots["common_mix_volume_uL"] = sum(float(value) for value in component_values)
            assumptions.append("Calculated common_mix_volume_uL from enzyme, water, and primer volumes.")

    if _is_missing(normalized_slots.get("reaction_volume_uL")):
        if not _is_missing(normalized_slots.get("common_mix_volume_uL")) and not _is_missing(
            normalized_slots.get("template_volume_uL")
        ):
            normalized_slots["reaction_volume_uL"] = float(normalized_slots["common_mix_volume_uL"]) + float(
                normalized_slots["template_volume_uL"]
            )
            assumptions.append("Calculated reaction_volume_uL from common mix volume plus template volume.")
    elif not _is_missing(normalized_slots.get("common_mix_volume_uL")) and not _is_missing(
        normalized_slots.get("template_volume_uL")
    ):
        expected_reaction = float(normalized_slots["common_mix_volume_uL"]) + float(normalized_slots["template_volume_uL"])
        if abs(float(normalized_slots["reaction_volume_uL"]) - expected_reaction) > 1e-9:
            assumptions.append(
                "Warning: reaction_volume_uL does not equal common_mix_volume_uL + template_volume_uL; keeping the user/LLM value for validation."
            )

    if normalized_slots.get("premix_first"):
        _set_default(normalized_slots, "premix_labware", "PCR_premix_tube", assumptions)
        _set_default(normalized_slots, "premix_position", "A1", assumptions)
        if _is_missing(normalized_slots.get("common_mix_source_labware")):
            normalized_slots["common_mix_source_labware"] = normalized_slots["premix_labware"]
            assumptions.append("Defaulted common_mix_source_labware to premix_labware.")
        if _is_missing(normalized_slots.get("common_mix_source_position")):
            normalized_slots["common_mix_source_position"] = normalized_slots["premix_position"]
            assumptions.append("Defaulted common_mix_source_position to premix_position.")
    else:
        _set_default(normalized_slots, "common_mix_source_labware", "Reservoir_1", assumptions)
        _set_default(normalized_slots, "common_mix_source_position", "A1", assumptions)

    if int(float(normalized_slots.get("ntc_replicates", 0) or 0)) > 0:
        _set_default(normalized_slots, "ntc_water_source_labware", "Reservoir_1", assumptions)
        _set_default(normalized_slots, "ntc_water_source_position", "A2", assumptions)

    for labware_field in [
        "enzyme_source_labware",
        "water_source_labware",
        "forward_primer_source_labware",
        "reverse_primer_source_labware",
        "common_mix_source_labware",
        "ntc_water_source_labware",
    ]:
        if labware_field in normalized_slots:
            normalized_slots[labware_field] = _normalize_labware_name(str(normalized_slots[labware_field]))

    truly_missing = sorted(field for field in REQUIRED_PCR_FIELDS if _is_missing(normalized_slots.get(field)))
    for field in cleaned_llm_missing:
        if field in REQUIRED_PCR_FIELDS and _is_missing(normalized_slots.get(field)) and field not in truly_missing:
            truly_missing.append(field)

    return normalized_slots, truly_missing, assumptions


def _pcr_plan() -> dict[str, Any]:
    return {
        "task_type": "PCR_SETUP",
        "experiment_type": "PCR_SETUP",
        "planner": "rule_based",
        "confidence": 1.0,
        "slots": {},
        "missing_fields": [],
        "assumptions": [],
        "steps": [
            {"tool": "parse_pcr_config"},
            {"tool": "build_pcr_json"},
            {"tool": "calculate_common_mix"},
            {"tool": "generate_pcr_plate_map"},
            {"tool": "generate_pcr_worklist"},
            {"tool": "validate_pcr_setup"},
        ],
    }


def _od600_plan() -> dict[str, Any]:
    return {
        "task_type": "OD600_96_WELL",
        "experiment_type": "OD600_96_WELL",
        "planner": "rule_based",
        "confidence": 1.0,
        "slots": {},
        "missing_fields": [],
        "assumptions": [],
        "steps": [{"tool": "run_od600_pipeline"}],
    }


def _llm_planner_prompt(description: str) -> str:
    return f"""
You are LabMate's planner. Return strict JSON only. Do not include markdown.

Allowed task_type values:
- PCR_SETUP
- OD600_96_WELL
- GROWTH_CURVE_ANALYSIS
- UNKNOWN

Allowed tools:
- build_generic_pcr_design_from_slots
- expand_pcr_reaction_table
- validate_pcr_reaction_table
- generate_pcr_plate_map_from_reaction_table
- generate_pcr_worklist_from_reaction_table
- build_pcr_config_from_slots
- build_pcr_json
- calculate_common_mix
- generate_pcr_plate_map
- generate_pcr_worklist
- validate_pcr_setup
- run_od600_pipeline

Domain definitions:
- PCR: polymerase chain reaction setup with reaction volume, template, primers, enzyme/polymerase, water/master mix, NTC, destination PCR plate.
- OD600: bacterial/yeast optical-density plate experiment, usually 96-well, with medium, samples, blanks, controls, treatments.
- NTC: no-template control; receives water instead of template.
- premix: enzyme/water/forward primer/reverse primer or master mix combined before distribution.
- template: sample DNA added to each PCR reaction.
- primer pair: one forward primer plus one reverse primer. Multiple primer pairs mean separate premixes by primer pair.
- reaction_table mode: a generic PCR design where Python expands templates x primer pairs x replicates into an editable reaction table before worklist generation.

Rules:
- The LLM must NOT generate plate maps, Hamilton worklists, or validation reports.
- The LLM only selects tools and extracts slots or generic PCR design JSON.
- If task_type is PCR_SETUP and required PCR fields are missing, list them in missing_fields.
- For PCR_SETUP use these steps exactly for the first pass:
  build_generic_pcr_design_from_slots, expand_pcr_reaction_table, validate_pcr_reaction_table
- Do not include generate_pcr_worklist_from_reaction_table in the first pass. The app calls it only after the user confirms the editable reaction table.
- For OD600_96_WELL use this step exactly: run_od600_pipeline

Examples:
- "有十一对引物" means slots.primer_pair_count = 11
- "8个模板" means slots.template_count = 8
- "20 uL体系，1 uL template" means slots.reaction_volume_uL = 20 and slots.template_volume_uL = 1
- "酶9微升，水5微升，上下引物各1微升，模板4微升" means enzyme_volume_uL=9, water_volume_uL=5, forward_primer_volume_uL=1, reverse_primer_volume_uL=1, template_volume_uL=4
- "酶从reservoir A1吸出" means enzyme_source_labware="Reservoir", enzyme_source_position="A1"
- "上引物从reservoir A3吸出，下引物从reservoir A4吸出" means forward_primer_source_labware="Reservoir", forward_primer_source_position="A3", reverse_primer_source_labware="Reservoir", reverse_primer_source_position="A4"

Required output JSON schema:
{{
  "task_type": "PCR_SETUP | OD600_96_WELL | GROWTH_CURVE_ANALYSIS | UNKNOWN",
  "mode": "simple_pcr | reaction_table",
  "confidence": 0.0,
  "slots": {{}},
  "reagents": [],
  "reaction_recipe": [],
  "reaction_design": {{}},
  "plate_layout": {{}},
  "operations": [],
  "missing_fields": [],
  "assumptions": [],
  "steps": []
}}

Generic PCR reaction_table schema example:
{{
  "task_type": "PCR_SETUP",
  "mode": "reaction_table",
  "reagents": [
    {{"name": "Enzyme", "source_labware": "Reservoir_1", "source_position": "A1"}},
    {{"name": "Water", "source_labware": "Reservoir_1", "source_position": "A2"}},
    {{"name": "ForwardPrimer", "source_labware": "Reservoir_1", "source_position": "A3"}},
    {{"name": "ReversePrimer", "source_labware": "Reservoir_1", "source_position": "A4"}},
    {{"name": "Template", "source_labware": "SampleRack_1", "source_position": "B1"}}
  ],
  "reaction_recipe": [
    {{"component": "Enzyme", "volume_uL": 9}},
    {{"component": "Water", "volume_uL": 5}},
    {{"component": "ForwardPrimer", "volume_uL": 1}},
    {{"component": "ReversePrimer", "volume_uL": 1}},
    {{"component": "Template", "volume_uL": 4}}
  ],
  "reaction_design": {{
    "templates": 11,
    "primer_pairs": 5,
    "replicates": 1,
    "ntc": 0,
    "layout": "all_templates_by_all_primer_pairs"
  }},
  "plate_layout": {{
    "plate_type": "96_well",
    "destination_labware": "PCR_plate_96",
    "start_well": "A1",
    "order": "primer_pair_major"
  }},
  "operations": [
    {{"op": "make_premix", "scope": "per_primer_pair"}},
    {{"op": "dispense_premix_to_plate"}},
    {{"op": "add_template"}},
    {{"op": "validate"}}
  ],
  "missing_fields": []
}}

Recommended PCR slot names:
template_count, primer_pair_count, reaction_volume_uL, template_volume_uL,
enzyme_volume_uL, water_volume_uL, forward_primer_volume_uL, reverse_primer_volume_uL,
ntc_replicates, common_mix_source_labware, common_mix_source_position,
enzyme_source_labware, enzyme_source_position, water_source_labware, water_source_position,
forward_primer_source_labware, forward_primer_source_position,
reverse_primer_source_labware, reverse_primer_source_position,
template_source_labware, template_start_position,
ntc_water_source_labware, ntc_water_source_position,
destination_labware, destination_start_well, premix_first, overage_percent,
destination_layout.

User description:
{description}
""".strip()


def _call_openai_planner(prompt: str, api_key: str | None, model: str | None) -> dict[str, Any]:
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Missing OpenAI API key.")
    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-4.1"),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "temperature": 0,
        "text": {"format": {"type": "json_object"}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI planner request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI planner request failed: {exc}") from exc
    return _load_plan_json(_extract_response_text(response_payload), "OpenAI")


def _call_gemini_planner(prompt: str, api_key: str | None, model: str | None) -> dict[str, Any]:
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing Gemini API key.")
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("Gemini planner request timed out.") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini planner request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini planner request failed: {exc}") from exc
    return _load_plan_json(_extract_gemini_text(response_payload), "Gemini")


def _call_openai_compatible_planner(
    prompt: str,
    api_key: str | None,
    model: str,
    url: str,
    provider_name: str,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError(f"Missing {provider_name} API key.")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"{provider_name} planner request timed out.") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider_name} planner request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{provider_name} planner request failed: {exc}") from exc
    raw_text = response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _load_plan_json(raw_text, provider_name)


def _load_plan_json(raw_text: str, provider_name: str) -> dict[str, Any]:
    if not raw_text:
        raise RuntimeError(f"{provider_name} planner response did not contain text.")
    try:
        return json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider_name} planner returned non-JSON output: {raw_text}") from exc


def _normalize_agent_plan(plan: dict[str, Any]) -> dict[str, Any]:
    task_type = str(plan.get("task_type") or plan.get("experiment_type") or "UNKNOWN")
    normalized = {
        "task_type": task_type,
        "experiment_type": task_type,
        "planner": "llm",
        "confidence": float(plan.get("confidence", 0) or 0),
        "mode": plan.get("mode", "simple_pcr" if task_type == "PCR_SETUP" else ""),
        "slots": plan.get("slots") if isinstance(plan.get("slots"), dict) else {},
        "reagents": plan.get("reagents") if isinstance(plan.get("reagents"), list) else [],
        "reaction_recipe": plan.get("reaction_recipe") if isinstance(plan.get("reaction_recipe"), list) else [],
        "reaction_design": plan.get("reaction_design") if isinstance(plan.get("reaction_design"), dict) else {},
        "plate_layout": plan.get("plate_layout") if isinstance(plan.get("plate_layout"), dict) else {},
        "operations": plan.get("operations") if isinstance(plan.get("operations"), list) else [],
        "missing_fields": plan.get("missing_fields") if isinstance(plan.get("missing_fields"), list) else [],
        "assumptions": plan.get("assumptions") if isinstance(plan.get("assumptions"), list) else [],
        "steps": plan.get("steps") if isinstance(plan.get("steps"), list) else [],
    }
    if task_type == "PCR_SETUP":
        normalized["steps"] = [{"tool": tool} for tool in [
            "build_generic_pcr_design_from_slots",
            "expand_pcr_reaction_table",
            "validate_pcr_reaction_table",
        ]]
    elif task_type == "OD600_96_WELL":
        normalized["steps"] = [{"tool": "run_od600_pipeline"}]
    return normalized


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _set_default(slots: dict[str, Any], key: str, value: Any, assumptions: list[str]) -> None:
    if _is_missing(slots.get(key)):
        slots[key] = value
        assumptions.append(f"Defaulted {key} to {value}.")


def _text_mentions_any(text: str, needles: list[str]) -> bool:
    lower_text = text.lower()
    return any(needle.lower() in lower_text for needle in needles)


def _normalize_labware_name(name: str) -> str:
    if name.strip().lower() == "reservoir":
        return "Reservoir_1"
    return name


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
