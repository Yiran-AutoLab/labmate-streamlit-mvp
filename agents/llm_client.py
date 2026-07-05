from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SECRET_PATH = Path(__file__).resolve().parents[1] / ".streamlit" / "labmate_secrets.json"


def load_saved_llm_settings() -> dict[str, str]:
    if not SECRET_PATH.exists():
        return {}
    try:
        payload = json.loads(SECRET_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def save_llm_settings(provider: str, model: str, api_key: str) -> None:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(
        json.dumps({"provider": provider, "model": model, "api_key": api_key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass


def resolve_api_key(provider: str, explicit_api_key: str = "") -> str:
    if explicit_api_key:
        return explicit_api_key
    env_name = "OPENROUTER_API_KEY" if provider == "OpenRouter" else "OPENAI_API_KEY"
    env_value = os.environ.get(env_name, "")
    if env_value:
        return env_value
    return load_saved_llm_settings().get("api_key", "")


def chat_json(
    *,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int = 90,
) -> Any:
    raw_content = _chat_raw(
        provider=provider,
        model=model,
        api_key=api_key,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout_seconds=timeout_seconds,
    )
    if not raw_content:
        raise RuntimeError(f"{provider} response did not contain message content.")
    try:
        return parse_jsonish(raw_content)
    except json.JSONDecodeError as exc:
        repaired = _repair_json_with_llm(
            provider=provider,
            model=model,
            api_key=api_key,
            bad_json=raw_content,
            parse_error=str(exc),
            timeout_seconds=timeout_seconds,
        )
        return parse_jsonish(repaired)


def chat_text(
    *,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int = 90,
) -> str:
    payload = chat_json(
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt=system_prompt + "\nReturn JSON with one field named review.",
        user_prompt=user_prompt,
        timeout_seconds=timeout_seconds,
    )
    return str(payload.get("review", payload))


def parse_jsonish(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _chat_raw(
    *,
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout_seconds: int,
) -> str:
    if not api_key:
        raise ValueError("Missing LLM API key. Enter it in the sidebar or enable local key memory.")

    if provider == "OpenRouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "LabMate Layout Agent MVP",
        }
    elif provider == "OpenAI":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider} request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{provider} request failed: {exc}") from exc

    return response_payload.get("choices", [{}])[0].get("message", {}).get("content", "")


def _repair_json_with_llm(
    *,
    provider: str,
    model: str,
    api_key: str,
    bad_json: str,
    parse_error: str,
    timeout_seconds: int,
) -> str:
    clipped = bad_json[:60000]
    return _chat_raw(
        provider=provider,
        model=model,
        api_key=api_key,
        messages=[
            {
                "role": "system",
                "content": (
                    "You repair malformed JSON. Return ONLY valid JSON. "
                    "Do not add explanations, markdown, comments, or new fields."
                ),
            },
            {
                "role": "user",
                "content": f"Parse error: {parse_error}\n\nMalformed JSON to repair:\n{clipped}",
            },
        ],
        timeout_seconds=timeout_seconds,
    )
