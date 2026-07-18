from __future__ import annotations

import os


LOCAL_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def allowed_origins() -> list[str]:
    configured = os.environ.get("ALLOWED_ORIGINS", "")
    if not configured.strip():
        return LOCAL_ORIGINS
    return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]


def resolve_provider_api_key(provider: str, client_api_key: str | None = None) -> str | None:
    if provider == "Mock":
        return None

    env_names = {
        "OpenRouter": "OPENROUTER_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
    }
    env_name = env_names.get(provider)
    server_key = os.environ.get(env_name, "") if env_name else ""
    if server_key:
        return server_key

    if env_bool("ALLOW_CLIENT_API_KEYS") and client_api_key:
        return client_api_key
    return None

