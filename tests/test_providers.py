"""Tests for the provider layer: chain resolution, failover, CLI parsing."""

from __future__ import annotations

import pytest

from wtd.config import WTDConfig
from wtd.providers import (
    Availability,
    ClaudeCodeProvider,
    GenerationResult,
    LLMProvider,
    ProviderError,
    ProviderRouter,
    provider_chain,
)
from wtd.providers.anthropic_api import thinking_kwargs
from wtd.providers.claude_code import cli_model_alias


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "WTD_LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


class TestChainResolution:
    def test_auto_resolves_claude_code_then_anthropic(self):
        chain = provider_chain(WTDConfig(llm_provider="auto"))
        assert [p.name for p in chain] == ["claude-code", "anthropic"]

    def test_explicit_provider_is_single_lane(self):
        for name in ("claude-code", "anthropic", "ollama", "openai"):
            chain = provider_chain(WTDConfig(llm_provider=name))
            assert [p.name for p in chain] == [name]


class TestClaudeCodeProvider:
    def test_unavailable_without_cli(self):
        provider = ClaudeCodeProvider(WTDConfig(claude_cli_path="/nonexistent/claude"))
        availability = provider.available()
        assert availability.available is False
        assert "claude CLI not found" in availability.reason

    def test_oauth_token_scrubs_api_key_from_child_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
        provider = ClaudeCodeProvider(
            WTDConfig(claude_code_oauth_token="oauth-token")
        )
        env = provider._build_env()
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
        assert "ANTHROPIC_API_KEY" not in env

    def test_without_token_env_passes_through(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-kept")
        provider = ClaudeCodeProvider(WTDConfig())
        env = provider._build_env()
        assert env.get("ANTHROPIC_API_KEY") == "sk-kept"

    def test_parse_output_success(self):
        provider = ClaudeCodeProvider(WTDConfig())
        payload = provider._parse_output(
            '{"type":"result","is_error":false,"result":"hello",'
            '"total_cost_usd":0.01,"usage":{"input_tokens":10,"output_tokens":5}}'
        )
        assert payload["result"] == "hello"

    def test_parse_output_error_flag_raises(self):
        provider = ClaudeCodeProvider(WTDConfig())
        with pytest.raises(ProviderError, match="claude reported an error"):
            provider._parse_output('{"is_error": true, "result": "credit exhausted"}')

    def test_parse_output_garbage_raises(self):
        provider = ClaudeCodeProvider(WTDConfig())
        with pytest.raises(ProviderError, match="unparseable"):
            provider._parse_output("not json at all")

    def test_model_alias_mapping(self):
        assert cli_model_alias("claude-opus-5") == "opus"
        assert cli_model_alias("claude-sonnet-5") == "sonnet"
        assert cli_model_alias("claude-haiku-4-5") == "haiku"
        assert cli_model_alias("some-custom-model") == "some-custom-model"

    def test_command_disables_tools_and_sessions(self):
        provider = ClaudeCodeProvider(WTDConfig())
        # Only meaningful when the CLI exists on this machine.
        if not provider.available().available:
            pytest.skip("claude CLI not installed")
        cmd = provider._build_command("sys prompt", "claude-opus-5")
        assert "-p" in cmd
        assert "--output-format" in cmd and "json" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == ""
        assert "--no-session-persistence" in cmd
        assert "--system-prompt" in cmd


class TestThinkingKwargs:
    def test_adaptive_for_current_families(self):
        for model in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5",
                      "claude-opus-4-8"):
            assert thinking_kwargs(model) == {"thinking": {"type": "adaptive"}}

    def test_omitted_for_older_models(self):
        assert thinking_kwargs("claude-haiku-4-5") == {}
        assert thinking_kwargs("gpt-x") == {}


class _StubProvider(LLMProvider):
    def __init__(self, name: str, *, available: bool = True,
                 error: ProviderError | None = None, text: str = "ok"):
        self.name = name
        self._available = available
        self._error = error
        self._text = text
        self.calls = 0

    def available(self) -> Availability:
        return Availability(self._available, "stub")

    async def generate(self, prompt, system="", *, model=None, max_tokens=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return GenerationResult(text=self._text, provider=self.name, model="m")


class TestRouterFailover:
    def make_router(self, *providers: _StubProvider) -> ProviderRouter:
        router = ProviderRouter.__new__(ProviderRouter)
        router.config = WTDConfig()
        router.chain = list(providers)
        return router

    async def test_first_available_lane_serves(self):
        first = _StubProvider("claude-code", text="from-first")
        second = _StubProvider("anthropic", text="from-second")
        result = await self.make_router(first, second).generate("hi")
        assert result.text == "from-first"
        assert second.calls == 0

    async def test_unavailable_lane_skipped(self):
        first = _StubProvider("claude-code", available=False)
        second = _StubProvider("anthropic", text="fallback")
        result = await self.make_router(first, second).generate("hi")
        assert result.provider == "anthropic"

    async def test_retryable_error_fails_over(self):
        first = _StubProvider(
            "claude-code", error=ProviderError("claude-code", "timeout", retryable=True)
        )
        second = _StubProvider("anthropic", text="rescued")
        result = await self.make_router(first, second).generate("hi")
        assert result.text == "rescued"

    async def test_non_retryable_error_stops_chain(self):
        first = _StubProvider(
            "claude-code",
            error=ProviderError("claude-code", "bad request", retryable=False),
        )
        second = _StubProvider("anthropic")
        with pytest.raises(ProviderError, match="all lanes failed"):
            await self.make_router(first, second).generate("hi")
        assert second.calls == 0

    async def test_preferred_lane_moves_to_front(self):
        first = _StubProvider("claude-code", text="first")
        second = _StubProvider("anthropic", text="second")
        result = await self.make_router(first, second).generate(
            "hi", preferred="anthropic"
        )
        assert result.text == "second"
        assert first.calls == 0

    async def test_all_lanes_failing_raises_with_detail(self):
        first = _StubProvider("claude-code", error=ProviderError("claude-code", "a"))
        second = _StubProvider("anthropic", error=ProviderError("anthropic", "b"))
        with pytest.raises(ProviderError, match="all lanes failed"):
            await self.make_router(first, second).generate("hi")
