"""Provider abstraction for the WTD platform.

Every LLM call in WTD goes through an :class:`LLMProvider`. Two providers
form the default chain:

1. ``claude-code`` — Claude Code running headless, authenticated with a
   Claude Code OAuth token (``CLAUDE_CODE_OAUTH_TOKEN``) or an existing
   ``claude`` CLI login. This is the **default lane**: it rides a Claude
   subscription instead of metered API billing.
2. ``anthropic`` — the Anthropic API via the official ``anthropic`` SDK,
   authenticated with ``ANTHROPIC_API_KEY``. This is the **fallback lane**.

Legacy providers (``ollama``, ``openai``) remain available but only when
selected explicitly; they are never part of the automatic chain.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """An LLM provider failed to produce a result.

    ``retryable`` marks failures where another lane (or a later retry on the
    same lane) could plausibly succeed: rate limits, timeouts, transient
    network or server errors, missing credentials. Non-retryable errors
    (e.g. an invalid request) should not trigger failover.
    """

    def __init__(self, provider: str, message: str, *, retryable: bool = True):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable


@dataclass
class Availability:
    """Result of a provider availability probe."""

    available: bool
    reason: str = ""


@dataclass
class GenerationResult:
    """A completed generation, with enough accounting for load balancing."""

    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(ABC):
    """Base interface implemented by every WTD provider."""

    #: Stable identifier used in config, lanes, and the run ledger.
    name: str = "base"

    @abstractmethod
    def available(self) -> Availability:
        """Cheaply report whether this provider could serve a request.

        Must not perform network calls; inspect configuration and the local
        environment only.
        """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        """Generate a completion for ``prompt``.

        Raises :class:`ProviderError` on failure — providers never return
        error strings as if they were model output.
        """


# Rough $/MTok pricing for cost *estimates* in the run ledger. The
# authoritative source is the invoice, not this table; unknown models fall
# back to the Opus-tier rate so estimates err on the conservative side.
MODEL_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_DEFAULT_PRICING = (5.0, 25.0)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the metered-API cost of a call in USD."""
    in_rate, out_rate = MODEL_PRICING_PER_MTOK.get(model, _DEFAULT_PRICING)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
