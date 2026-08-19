"""Provider resolution and failover.

``provider_chain`` builds the ordered list of lanes WTD may use:

* ``auto`` (the default): Claude Code OAuth first, Anthropic API second.
* an explicit provider name: exactly that provider, no failover.

``ProviderRouter.generate`` walks the chain, failing over on retryable
errors, and reports which lane served the request. The fleet's capacity
balancer uses the same chain as its lane order.
"""

from __future__ import annotations

from wtd.config import WTDConfig, get_config
from wtd.providers.anthropic_api import AnthropicApiProvider
from wtd.providers.base import GenerationResult, LLMProvider, ProviderError
from wtd.providers.claude_code import ClaudeCodeProvider
from wtd.providers.legacy import OllamaProvider, OpenAIProvider

AUTO_CHAIN = ("claude-code", "anthropic")


def build_provider(name: str, config: WTDConfig) -> LLMProvider:
    """Instantiate a provider by name."""
    providers: dict[str, type[LLMProvider]] = {
        ClaudeCodeProvider.name: ClaudeCodeProvider,
        AnthropicApiProvider.name: AnthropicApiProvider,
        OllamaProvider.name: OllamaProvider,
        OpenAIProvider.name: OpenAIProvider,
    }
    provider_cls = providers.get(name)
    if provider_cls is None:
        raise ValueError(f"unknown provider: {name!r}")
    return provider_cls(config)  # type: ignore[call-arg]


def provider_chain(config: WTDConfig | None = None) -> list[LLMProvider]:
    """The ordered provider lanes for the current configuration."""
    config = config or get_config()
    if config.llm_provider == "auto":
        return [build_provider(name, config) for name in AUTO_CHAIN]
    return [build_provider(config.llm_provider, config)]


def describe_chain(config: WTDConfig | None = None) -> list[dict[str, str | bool]]:
    """Availability report for every lane, for status surfaces."""
    report: list[dict[str, str | bool]] = []
    for provider in provider_chain(config):
        availability = provider.available()
        report.append(
            {
                "provider": provider.name,
                "available": availability.available,
                "detail": availability.reason,
            }
        )
    return report


class ProviderRouter:
    """Generate through the first healthy lane, failing over as needed."""

    def __init__(self, config: WTDConfig | None = None):
        self.config = config or get_config()
        self.chain = provider_chain(self.config)

    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        preferred: str | None = None,
    ) -> GenerationResult:
        """Try each lane in order; raise the last error if all fail.

        ``preferred`` moves a named lane to the front (used by the fleet
        balancer, which picks lanes by remaining capacity).
        """
        chain = list(self.chain)
        if preferred:
            chain.sort(key=lambda p: p.name != preferred)

        errors: list[ProviderError] = []
        for provider in chain:
            availability = provider.available()
            if not availability.available:
                errors.append(
                    ProviderError(provider.name, availability.reason)
                )
                continue
            try:
                return await provider.generate(
                    prompt, system, model=model, max_tokens=max_tokens
                )
            except ProviderError as exc:
                errors.append(exc)
                if not exc.retryable:
                    break

        detail = "; ".join(str(e) for e in errors) or "no providers configured"
        raise ProviderError("router", f"all lanes failed: {detail}", retryable=False)
