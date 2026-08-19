"""WTD LLM providers.

Default chain: Claude Code OAuth (subscription) → Anthropic API (metered).
"""

from wtd.providers.anthropic_api import AnthropicApiProvider
from wtd.providers.base import (
    Availability,
    GenerationResult,
    LLMProvider,
    ProviderError,
    estimate_cost_usd,
)
from wtd.providers.claude_code import ClaudeCodeProvider
from wtd.providers.legacy import OllamaProvider, OpenAIProvider
from wtd.providers.router import (
    AUTO_CHAIN,
    ProviderRouter,
    build_provider,
    describe_chain,
    provider_chain,
)

__all__ = [
    "AUTO_CHAIN",
    "AnthropicApiProvider",
    "Availability",
    "ClaudeCodeProvider",
    "GenerationResult",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderError",
    "ProviderRouter",
    "build_provider",
    "describe_chain",
    "estimate_cost_usd",
    "provider_chain",
]
