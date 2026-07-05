"""Parse natural language or JSON experiment descriptions into a design dict."""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from copy import deepcopy
from itertools import combinations
from typing import Any


EXAMPLE_DESIGN: dict[str, Any] = {
    "experiment_name": "OD600 pilot",
    "experiment_type": "OD600_96_WELL",
    "dest_labware": "OD600_plate_96",
    "medium": {
        "volume_uL": 180,
        "source_labware": "Reservoir_1",
        "source_position": "A1",
        "liquid_class": "Water",
    },
    "sample": {
        "volume_uL": 20,
        "liquid_class": "Water",
    },
    "blank_wells": ["A1", "A2"],
    "groups": [
        {
            "name": "Control",
            "role": "control",
            "replicates": 3,
            "source_labware": "SampleRack_1",
            "source_position": "B1",
        },
        {
            "name": "Treatment",
            "role": "treatment",
            "replicates": 3,
            "source_labware": "SampleRack_1",
            "source_position": "B2",
        },
    ],
    "worklist": {
        "tip_strategy": "new_tip_each_transfer",
        "mix_after": "No",
    },
}


GROUP_SOURCE_RE = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?:source\s+labware|source_labware|样品来源板位|样品来源耗材)\s*[:=：]?\s*(?P<labware>[A-Za-z0-9_\-]+)\s+"
    r"(?:source\s+position|source_position|来源孔位)\s*[:=：]?\s*(?P<position>[A-Ha-h](?:[1-9]|1[0-2]))\s*$",
    re.IGNORECASE,
)
WELL_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Ha-h](?:0?[1-9]|1[0-2]))(?![A-Za-z0-9])")
LABWARE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
CLAUSE_SPLIT_RE = re.compile(r"[\n。；;，,]")


def parse_experiment(text: str, fallback_design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse user text into a normalized experiment design."""

    base = deepcopy(fallback_design or EXAMPLE_DESIGN)
    text = (text or "").strip()
    if not text:
        return normalize_design(base)

    json_design = _try_parse_json(text)
    if json_design is not None:
        return normalize_design(_deep_merge(base, json_design))

    parsed = deepcopy(base)
    parsed["experiment_name"] = _match_text(
        text,
        [r"(?:experiment|name)\s*[:=]\s*([^\n;]+)", r"实验(?:名称)?\s*[:：]\s*([^\n；]+)"],
        parsed["experiment_name"],
    )
    parsed["blank_wells"] = _parse_wells(text) or parsed["blank_wells"]
    parsed["medium"]["volume_uL"] = _parse_volume_for_keywords(
        text,
        keywords=["medium", "media", "培养基"],
        default=parsed["medium"]["volume_uL"],
    )
    parsed["sample"]["volume_uL"] = _parse_volume_for_keywords(
        text,
        keywords=["sample", "inoculum", "culture", "样品", "菌液", "样本"],
        default=parsed["sample"]["volume_uL"],
    )

    medium_source = _parse_source_for_keywords(text, keywords=["medium", "media", "培养基"])
    if medium_source:
        parsed["medium"]["source_labware"] = medium_source[0]
        parsed["medium"]["source_position"] = medium_source[1]

    common_reps = _match_int(
        text,
        [
            r"replicates?\s*[:=]?\s*(\d+)",
            r"重复\s*(\d+)",
            r"各\s*(\d+)\s*(?:个)?(?:重复|复孔)",
            r"每组\s*(\d+)\s*(?:个)?(?:重复|复孔)",
            r"(\d+)\s*(?:replicates?|reps|个重复|个复孔|重复|复孔)",
        ],
        None,
    )

    pairwise_groups = _parse_pairwise_strain_groups(text, common_reps or 3)
    if pairwise_groups:
        parsed["groups"] = pairwise_groups
        _apply_component_sources(parsed["groups"], text)
    else:
        group_names = _parse_group_names(text)
        if group_names:
            parsed["groups"] = [
                {
                    "name": name,
                    "role": _role_for_group(name),
                    "replicates": common_reps or 3,
                    "source_labware": "SampleRack_1",
                    "source_position": f"B{i + 1}",
                }
                for i, name in enumerate(group_names)
            ]
        elif common_reps is not None:
            for group in parsed["groups"]:
                group["replicates"] = common_reps

        group_sources_found = _apply_group_sources_from_text(parsed["groups"], text)
        if not group_sources_found:
            _apply_global_sample_source(parsed["groups"], text)

    return normalize_design(parsed)


def parse_with_llm(text: str) -> dict[str, Any]:
    """Parse text with the OpenAI Responses API and return a normalized design."""

    return parse_with_provider(text, provider="openai")


def parse_with_provider(
    text: str,
    provider: str,
    fallback_design: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provider = provider.lower().strip()
    if provider in {"rules", "local", "local rules"}:
        return parse_experiment(text, fallback_design=fallback_design)
    if provider == "openai":
        return parse_with_openai(text, fallback_design=fallback_design, api_key=api_key, model=model)
    if provider == "gemini":
        return parse_with_gemini(text, fallback_design=fallback_design, api_key=api_key, model=model)
    if provider == "groq":
        return parse_with_groq(text, fallback_design=fallback_design, api_key=api_key, model=model)
    if provider == "openrouter":
        return parse_with_openrouter(text, fallback_design=fallback_design, api_key=api_key, model=model)
    raise ValueError(f"Unsupported parser provider: {provider}")


def parse_with_openai(
    text: str,
    fallback_design: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Use OpenAI structured outputs to convert free text into design JSON."""

    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Missing OpenAI API key. Set OPENAI_API_KEY or enter it in the Streamlit sidebar.")

    fallback = normalize_design(fallback_design or EXAMPLE_DESIGN)
    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-4.1"),
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": _llm_system_prompt(fallback),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "labmate_experiment_design",
                "strict": True,
                "schema": _experiment_design_schema(strict_openai=True),
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    raw_text = _extract_response_text(response_payload)
    if not raw_text:
        raise RuntimeError("OpenAI API response did not contain output_text.")

    return _normalize_llm_json(raw_text, fallback, "OpenAI")


def parse_with_gemini(
    text: str,
    fallback_design: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Use Gemini API structured output to convert free text into design JSON."""

    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing Gemini API key. Set GEMINI_API_KEY or enter it in the Streamlit sidebar.")

    fallback = normalize_design(fallback_design or EXAMPLE_DESIGN)
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"{_llm_system_prompt(fallback)}\n\nUser experiment description:\n{text}",
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _experiment_design_schema(strict_openai=False),
        },
    }
    response_payload = None
    last_error: Exception | None = None
    retry_delays = [0, 2, 6]
    for attempt, delay_seconds in enumerate(retry_delays):
        if delay_seconds:
            time.sleep(delay_seconds)
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Gemini API request failed: HTTP {exc.code}: {detail}")
            if exc.code in {502, 503, 504} and attempt < len(retry_delays) - 1:
                continue
            raise last_error from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RuntimeError("Gemini API request timed out after 120 seconds.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Gemini API request failed: {exc}") from exc

    if response_payload is None:
        raise RuntimeError(str(last_error or "Gemini API request failed."))

    return _normalize_llm_json(_extract_gemini_text(response_payload), fallback, "Gemini")


def parse_with_groq(
    text: str,
    fallback_design: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Use Groq OpenAI-compatible chat completions to convert text into JSON."""

    api_key = api_key or os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Missing Groq API key. Set GROQ_API_KEY or enter it in the Streamlit sidebar.")

    fallback = normalize_design(fallback_design or EXAMPLE_DESIGN)
    payload = {
        "model": model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "messages": [
            {"role": "system", "content": _llm_system_prompt(fallback)},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Groq API request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Groq API request failed: {exc}") from exc

    raw_text = response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _normalize_llm_json(raw_text, fallback, "Groq")


def parse_with_openrouter(
    text: str,
    fallback_design: dict[str, Any] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Use OpenRouter OpenAI-compatible chat completions to convert text into JSON."""

    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("Missing OpenRouter API key. Set OPENROUTER_API_KEY or enter it in the Streamlit sidebar.")

    fallback = normalize_design(fallback_design or EXAMPLE_DESIGN)
    payload = {
        "model": model or os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
        "messages": [
            {"role": "system", "content": _llm_system_prompt(fallback)},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "LabMate OD600 MVP",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenRouter API request failed: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("OpenRouter API request timed out after 90 seconds.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter API request failed: {exc}") from exc

    raw_text = response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    return _normalize_llm_json(raw_text, fallback, "OpenRouter")


def _llm_system_prompt(fallback_design: dict[str, Any]) -> str:
    return (
        "You convert natural-language OD600 96-well plate experiment descriptions into strict JSON. "
        "Only design OD600 experiments on one 96-well plate. Preserve user-specified strain/group names. "
        "If the user asks for pairwise/two-by-two combinations of strains, expand them into treatment groups "
        "such as Ld+Cs, Ld+Mi, Cs+Mi. For combination groups, include components for each strain. "
        "If source positions are not specified for strains, assign strain components in order to SampleRack_1 B1, B2, B3, etc. "
        "If a value is missing, use these defaults exactly: "
        f"{json.dumps(fallback_design, ensure_ascii=False)}. "
        "Return only JSON matching the schema."
    )


def _experiment_design_schema(strict_openai: bool = True) -> dict[str, Any]:
    component_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "source_labware": {"type": "string"},
            "source_position": {"type": "string"},
        },
        "required": ["name", "source_labware", "source_position"],
    }
    if strict_openai:
        component_schema["additionalProperties"] = False

    group_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "role": {"type": "string", "enum": ["control", "treatment", "blank"]},
            "replicates": {"type": "integer", "minimum": 0, "maximum": 96},
            "source_labware": {"type": "string"},
            "source_position": {"type": "string"},
            "components": {"type": "array", "items": component_schema},
        },
        "required": ["name", "role", "replicates", "source_labware", "source_position", "components"],
    }
    if strict_openai:
        group_schema["additionalProperties"] = False

    root_schema = {
        "type": "object",
        "properties": {
            "experiment_name": {"type": "string"},
            "experiment_type": {"type": "string", "enum": ["OD600_96_WELL"]},
            "dest_labware": {"type": "string"},
            "medium": {
                "type": "object",
                "properties": {
                    "volume_uL": {"type": "number", "minimum": 0},
                    "source_labware": {"type": "string"},
                    "source_position": {"type": "string"},
                    "liquid_class": {"type": "string"},
                },
                "required": ["volume_uL", "source_labware", "source_position", "liquid_class"],
            },
            "sample": {
                "type": "object",
                "properties": {
                    "volume_uL": {"type": "number", "minimum": 0},
                    "liquid_class": {"type": "string"},
                },
                "required": ["volume_uL", "liquid_class"],
            },
            "blank_wells": {"type": "array", "items": {"type": "string"}},
            "groups": {"type": "array", "items": group_schema},
            "worklist": {
                "type": "object",
                "properties": {
                    "tip_strategy": {"type": "string"},
                    "mix_after": {"type": "string"},
                },
                "required": ["tip_strategy", "mix_after"],
            },
        },
        "required": [
            "experiment_name",
            "experiment_type",
            "dest_labware",
            "medium",
            "sample",
            "blank_wells",
            "groups",
            "worklist",
        ],
    }
    if strict_openai:
        root_schema["additionalProperties"] = False
        root_schema["properties"]["medium"]["additionalProperties"] = False
        root_schema["properties"]["sample"]["additionalProperties"] = False
        root_schema["properties"]["worklist"]["additionalProperties"] = False
    return root_schema


def _extract_response_text(response_payload: dict[str, Any]) -> str:
    if isinstance(response_payload.get("output_text"), str):
        return response_payload["output_text"]

    for item in response_payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    return ""


def _extract_gemini_text(response_payload: dict[str, Any]) -> str:
    if isinstance(response_payload.get("output_text"), str):
        return response_payload["output_text"]
    for item in response_payload.get("output", []):
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                return content["text"]
    for candidate in response_payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                return part["text"]
    if response_payload.get("promptFeedback") or response_payload.get("candidates"):
        raise RuntimeError(f"Gemini response did not include text: {json.dumps(response_payload, ensure_ascii=False)[:1200]}")
    return ""


def _normalize_llm_json(raw_text: str, fallback: dict[str, Any], provider_name: str) -> dict[str, Any]:
    if not raw_text:
        raise RuntimeError(f"{provider_name} API response did not contain text output.")
    try:
        llm_design = json.loads(_strip_json_fences(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider_name} returned non-JSON output: {raw_text}") from exc
    return normalize_design(_deep_merge(fallback, llm_design))


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return text


def design_to_pretty_json(design: dict[str, Any]) -> str:
    return json.dumps(design, indent=2, ensure_ascii=False)


def normalize_design(design: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(EXAMPLE_DESIGN)
    normalized = _deep_merge(normalized, design or {})
    normalized["experiment_type"] = "OD600_96_WELL"
    normalized["blank_wells"] = [_normalize_well(well) for well in normalized.get("blank_wells", [])]

    for key in ("medium", "sample"):
        normalized[key]["volume_uL"] = float(normalized[key].get("volume_uL", 0))

    groups = []
    for idx, group in enumerate(normalized.get("groups", [])):
        components = _normalize_components(group, idx)
        groups.append(
            {
                "name": str(group.get("name") or f"Group {idx + 1}"),
                "role": str(group.get("role") or "treatment"),
                "replicates": int(group.get("replicates") or 0),
                "source_labware": ";".join(component["source_labware"] for component in components),
                "source_position": ";".join(component["source_position"] for component in components),
                "components": components,
            }
        )
    normalized["groups"] = groups
    return normalized


def _parse_pairwise_strain_groups(text: str, replicates: int) -> list[dict[str, Any]]:
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ["两两组合", "两两混合", "两两共培养", "pairwise", "pair-wise"]):
        return []

    strains = _parse_strain_list_for_pairwise(text)
    if len(strains) < 2:
        return []

    strain_sources = {
        strain: {
            "name": strain,
            "source_labware": "SampleRack_1",
            "source_position": f"B{i + 1}",
        }
        for i, strain in enumerate(strains)
    }

    groups = []
    for left, right in combinations(strains, 2):
        components = [deepcopy(strain_sources[left]), deepcopy(strain_sources[right])]
        groups.append(
            {
                "name": f"{left}+{right}",
                "role": "treatment",
                "replicates": replicates,
                "components": components,
            }
        )
    return groups


def _parse_strain_list_for_pairwise(text: str) -> list[str]:
    marker_match = re.search(r"两两组合|两两混合|两两共培养|pairwise|pair-wise", text, re.IGNORECASE)
    if not marker_match:
        return []
    before_marker = text[: marker_match.start()]
    candidates = re.findall(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_-]{0,12})(?![A-Za-z0-9])", before_marker)
    stopwords = {
        "OD",
        "OD600",
        "well",
        "plate",
        "control",
        "treatment",
        "blank",
        "sample",
        "medium",
        "media",
        "ul",
        "uL",
    }
    strains = [candidate for candidate in candidates if candidate not in stopwords]
    return list(dict.fromkeys(strains))


def _apply_component_sources(groups: list[dict[str, Any]], text: str) -> None:
    known_components = {
        component["name"]: component
        for group in groups
        for component in group.get("components", [])
    }
    for strain, component in known_components.items():
        for clause in _clauses(text):
            if strain.lower() not in clause.lower():
                continue
            source = _source_pair_in_text(clause)
            if source:
                component["source_labware"] = source[0]
                component["source_position"] = source[1]

    for group in groups:
        components = [known_components[component["name"]] for component in group.get("components", [])]
        group["components"] = deepcopy(components)


def _normalize_components(group: dict[str, Any], group_index: int) -> list[dict[str, str]]:
    raw_components = group.get("components") or []
    if not raw_components:
        raw_components = [
            {
                "name": str(group.get("name") or f"Group {group_index + 1}"),
                "source_labware": group.get("source_labware") or "SampleRack_1",
                "source_position": group.get("source_position") or f"B{group_index + 1}",
            }
        ]

    components = []
    for idx, component in enumerate(raw_components):
        components.append(
            {
                "name": str(component.get("name") or f"Component {idx + 1}"),
                "source_labware": str(component.get("source_labware") or group.get("source_labware") or "SampleRack_1"),
                "source_position": _normalize_well(
                    component.get("source_position") or group.get("source_position") or f"B{group_index + idx + 1}"
                ),
            }
        )
    return components


def _apply_global_sample_source(groups: list[dict[str, Any]], text: str) -> None:
    source_labware = _match_text(
        text,
        [
            r"(?:sample\s+)?source\s+labware\s*[:=]\s*([A-Za-z0-9_\-]+)",
            r"(?:sample_)?source_labware\s*[:=]\s*([A-Za-z0-9_\-]+)",
            r"样品来源耗材\s*[:：]\s*([A-Za-z0-9_\-]+)",
        ],
        None,
    )
    source_position = _match_text(
        text,
        [
            r"(?:sample\s+)?source\s+position\s*[:=]\s*([A-Ha-h](?:[1-9]|1[0-2]))",
            r"(?:sample_)?source_position\s*[:=]\s*([A-Ha-h](?:[1-9]|1[0-2]))",
            r"样品来源孔位\s*[:：]\s*([A-Ha-h](?:[1-9]|1[0-2]))",
        ],
        None,
    )
    if source_labware:
        for group in groups:
            group["source_labware"] = source_labware
    if source_position:
        for group in groups:
            group["source_position"] = _normalize_well(source_position)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))

    object_match = re.search(r"\{.*\}", text, re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _parse_wells(text: str) -> list[str]:
    blank_wells = []
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if not any(keyword in lowered for keyword in ["blank", "空白", "阴性对照"]):
            continue
        blank_wells.extend(_wells_in_text(sentence))
    return list(dict.fromkeys(blank_wells))


def _parse_group_names(text: str) -> list[str]:
    match = re.search(r"groups?\s*[:=]\s*([^\n;]+)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"分组\s*[:：]\s*([^\n；]+)", text)
    if match:
        return _clean_group_names(re.split(r",|，|/|\band\b|和|与|及", match.group(1)))

    if "对照" in text and ("处理" in text or "实验组" in text):
        return ["Control", "Treatment"]

    for sentence in _clauses(text):
        pair_match = re.search(
            r"([A-Za-z0-9_\-\u4e00-\u9fff]+?)组?\s*(?:和|与|及|,|，|/)\s*"
            r"([A-Za-z0-9_\-\u4e00-\u9fff]+?)组?\s*(?:各|每组)?\s*\d+\s*(?:个)?(?:重复|复孔|replicates?|reps)",
            sentence,
            re.IGNORECASE,
        )
        if pair_match:
            return _clean_group_names([pair_match.group(1), pair_match.group(2)])

    inferred = []
    lowered = text.lower()
    if "control" in lowered or "对照" in text:
        inferred.append("Control")
    if "treatment" in lowered or "处理" in text or "实验组" in text:
        inferred.append("Treatment")
    return inferred


def _apply_group_sources_from_text(groups: list[dict[str, Any]], text: str) -> bool:
    found = False
    for group in groups:
        group_name = str(group.get("name", ""))
        aliases = _group_aliases(group_name, str(group.get("role", "")))
        if not group_name:
            continue
        for line in text.splitlines():
            line_match = GROUP_SOURCE_RE.match(line.strip())
            if not line_match:
                continue
            if line_match.group("name").strip().lower() not in {alias.lower() for alias in aliases}:
                continue
            group["source_labware"] = line_match.group("labware")
            group["source_position"] = _normalize_well(line_match.group("position"))
            found = True
        for clause in _clauses(text):
            if not any(alias and alias.lower() in clause.lower() for alias in aliases):
                continue
            source = _source_pair_in_text(clause)
            if source:
                group["source_labware"] = source[0]
                group["source_position"] = source[1]
                found = True
    return found


def _parse_volume_for_keywords(text: str, keywords: list[str], default: float) -> float:
    for clause in _clauses(text):
        lowered = clause.lower()
        if not any(keyword.lower() in lowered for keyword in keywords):
            continue
        for keyword in keywords:
            unit = r"(uL|ul|μL|µL|微升|ml|mL|毫升)"
            before = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}\s*(?:的)?\s*{re.escape(keyword)}", clause, re.IGNORECASE)
            if before:
                return _volume_match_to_ul(before)
            after = re.search(rf"{re.escape(keyword)}[^\d]{{0,12}}(\d+(?:\.\d+)?)\s*{unit}", clause, re.IGNORECASE)
            if after:
                return _volume_match_to_ul(after)
        volumes = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(uL|ul|μL|µL|微升|ml|mL|毫升)", clause, re.IGNORECASE))
        if not volumes:
            continue
        keyword_positions = [
            match.start()
            for keyword in keywords
            for match in re.finditer(re.escape(keyword), clause, re.IGNORECASE)
        ]
        if not keyword_positions:
            continue
        nearest = min(volumes, key=lambda volume: min(abs(volume.start() - pos) for pos in keyword_positions))
        value = float(nearest.group(1))
        if nearest.group(2).lower() == "ml" or nearest.group(2) == "毫升":
            value *= 1000
        return value
    return float(default)


def _parse_source_for_keywords(text: str, keywords: list[str]) -> tuple[str, str] | None:
    for clause in _clauses(text):
        lowered = clause.lower()
        if not any(keyword.lower() in lowered for keyword in keywords):
            continue
        source = _source_pair_in_text(clause)
        if source:
            return source
    return None


def _source_pair_in_text(text: str) -> tuple[str, str] | None:
    wells = _wells_in_text(text)
    if not wells:
        return None
    well = wells[-1]
    before_well = text[: text.upper().rfind(well)]
    labware_candidates = [
        token
        for token in LABWARE_RE.findall(before_well)
        if token.lower() not in {"od600", "well", "plate", "medium", "media", "sample", "source", "labware", "position", "from"}
    ]
    if not labware_candidates:
        return None
    return labware_candidates[-1], well


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in CLAUSE_SPLIT_RE.split(text) if clause.strip()]


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"[\n。；;]", text) if sentence.strip()]


def _volume_match_to_ul(match: re.Match[str]) -> float:
    value = float(match.group(1))
    unit = match.group(2)
    if unit.lower() == "ml" or unit == "毫升":
        value *= 1000
    return value


def _wells_in_text(text: str) -> list[str]:
    return [_normalize_well(match.group(0)) for match in WELL_RE.finditer(text)]


def _clean_group_names(raw_names: list[str]) -> list[str]:
    names = []
    for raw_name in raw_names:
        name = raw_name.strip()
        name = re.sub(r"(?:groups?|分组)\s*[:：=]?", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(
            r"(?:(?:各|每组)?\s*\d+\s*(?:个)?(?:重复|复孔|replicates?|reps)).*$",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        name = re.sub(r"组$", "", name).strip()
        if not name:
            continue
        if name in {"对照", "空白对照"}:
            name = "Control"
        elif name in {"处理", "实验", "实验组"}:
            name = "Treatment"
        names.append(name)
    return list(dict.fromkeys(names))


def _role_for_group(name: str) -> str:
    lowered = name.lower()
    return "control" if "control" in lowered or "对照" in name else "treatment"


def _group_aliases(name: str, role: str) -> list[str]:
    aliases = [name, name.replace("组", "")]
    lowered = name.lower()
    if role == "control" or "control" in lowered or "对照" in name:
        aliases.extend(["Control", "control", "对照", "对照组"])
    if role == "treatment" or "treatment" in lowered or "处理" in name:
        aliases.extend(["Treatment", "treatment", "处理", "处理组", "实验组"])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _match_text(text: str, patterns: list[str], default: str | None) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return default


def _match_int(text: str, patterns: list[str], default: int | None) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return default


def _normalize_well(value: str) -> str:
    value = str(value).strip().upper()
    match = re.fullmatch(r"([A-H])0?([1-9]|1[0-2])", value)
    if not match:
        return value
    return f"{match.group(1)}{int(match.group(2))}"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
