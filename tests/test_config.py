"""Tests for ``wtd.config``."""

from __future__ import annotations

import pytest

from wtd.config import WTDConfig, get_config, reset_config


def test_defaults_are_local_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out of the box, WTD should default to local Ollama and not bind public hosts."""
    # Clear provider-related env vars so we measure pure defaults.
    for var in (
        "WTD_LLM_PROVIDER",
        "WTD_API_HOST",
        "WTD_API_PORT",
        "WTD_API_CORS_ORIGINS",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_config()

    cfg = get_config()

    assert cfg.llm_provider == "ollama"
    assert cfg.api_host == "127.0.0.1"
    assert cfg.api_port == 8787
    assert cfg.api_cors_origins == []


def test_env_var_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables with the WTD_ prefix should override defaults."""
    monkeypatch.setenv("WTD_LLM_PROVIDER", "openai")
    monkeypatch.setenv("WTD_API_PORT", "9999")
    reset_config()

    cfg = get_config()

    assert cfg.llm_provider == "openai"
    assert cfg.api_port == 9999


def test_get_config_is_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_config`` should cache the same instance until reset."""
    reset_config()
    first = get_config()
    second = get_config()
    assert first is second

    reset_config()
    third = get_config()
    assert third is not first


def test_cors_origins_accept_comma_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comma-separated CORS origins from the env should be parsed correctly."""
    monkeypatch.setenv(
        "WTD_API_CORS_ORIGINS",
        "http://localhost:3000, https://app.example.com",
    )
    reset_config()

    cfg = get_config()

    assert cfg.api_cors_origins == [
        "http://localhost:3000",
        "https://app.example.com",
    ]


def test_cors_origins_accept_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON-encoded list values should also work for parity with pydantic-settings."""
    monkeypatch.setenv("WTD_API_CORS_ORIGINS", '["http://a.example", "http://b.example"]')
    reset_config()

    cfg = get_config()

    assert cfg.api_cors_origins == ["http://a.example", "http://b.example"]


def test_invalid_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown providers should fail validation rather than silently default."""
    monkeypatch.setenv("WTD_LLM_PROVIDER", "definitely-not-a-real-provider")
    reset_config()

    with pytest.raises(Exception):
        WTDConfig()


def test_recursion_depth_bounds() -> None:
    """Recursion depth must be in the documented [1, 10] range."""
    with pytest.raises(Exception):
        WTDConfig(max_recursion_depth=0)
    with pytest.raises(Exception):
        WTDConfig(max_recursion_depth=11)
