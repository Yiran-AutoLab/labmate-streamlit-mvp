from __future__ import annotations

from backend.config import allowed_origins, resolve_provider_api_key
from backend.security import InMemoryRateLimiter, access_code_is_valid


def test_server_provider_key_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "server-secret")
    monkeypatch.setenv("ALLOW_CLIENT_API_KEYS", "true")
    assert resolve_provider_api_key("OpenRouter", "browser-secret") == "server-secret"


def test_client_provider_key_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_CLIENT_API_KEYS", raising=False)
    assert resolve_provider_api_key("OpenRouter", "browser-secret") is None


def test_access_code_and_dynamic_origins(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_ACCESS_CODE", "invite-only")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://demo.example, https://preview.example/")
    assert access_code_is_valid("invite-only")
    assert not access_code_is_valid("wrong")
    assert allowed_origins() == ["https://demo.example", "https://preview.example"]


def test_rate_limiter_releases_after_window() -> None:
    limiter = InMemoryRateLimiter(limit=2, window_seconds=10)
    assert limiter.check("client", now=0)[0]
    assert limiter.check("client", now=1)[0]
    allowed, retry_after = limiter.check("client", now=2)
    assert not allowed
    assert retry_after == 8
    assert limiter.check("client", now=11)[0]
