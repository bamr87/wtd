"""Anthropic API provider — the fallback WTD lane.

Uses the official ``anthropic`` SDK with ``ANTHROPIC_API_KEY`` (or
``WTD_ANTHROPIC_API_KEY``). Requests stream and default to
``claude-opus-5`` with adaptive thinking.

Server-side refusal fallbacks (``fallbacks: "default"`` with the
``server-side-fallback-2026-07-01`` beta) are enabled by default so a
safety decline re-routes inside the same call instead of failing a fleet
run; disable with ``WTD_ANTHROPIC_REFUSAL_FALLBACKS=false``. If the
installed SDK or the endpoint rejects the parameter, the provider
downgrades to a plain request once and remembers.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from wtd.providers.base import (
    Availability,
    GenerationResult,
    LLMProvider,
    ProviderError,
    estimate_cost_usd,
)

if TYPE_CHECKING:
    from wtd.config import WTDConfig

# Model families that take adaptive thinking. Older models (e.g. Haiku 4.5)
# still use budget_tokens; we simply omit `thinking` for those.
_ADAPTIVE_THINKING_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def thinking_kwargs(model: str) -> dict[str, Any]:
    """Request kwargs enabling adaptive thinking where supported."""
    if model.startswith(_ADAPTIVE_THINKING_PREFIXES):
        return {"thinking": {"type": "adaptive"}}
    return {}


class AnthropicApiProvider(LLMProvider):
    """Generate text through the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, config: "WTDConfig"):
        self.config = config
        self._fallbacks_supported = True

    def _api_key(self) -> str | None:
        import os

        return self.config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")

    def available(self) -> Availability:
        import os

        if self._api_key():
            return Availability(True, "ANTHROPIC_API_KEY configured")
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return Availability(True, "ANTHROPIC_AUTH_TOKEN configured")
        return Availability(
            False,
            "no Anthropic API credentials (set ANTHROPIC_API_KEY or "
            "WTD_ANTHROPIC_API_KEY)",
        )

    def _use_refusal_fallbacks(self, model: str) -> bool:
        return (
            self.config.anthropic_refusal_fallbacks
            and self._fallbacks_supported
            and model.startswith(("claude-fable-5", "claude-opus-5", "claude-mythos-5"))
        )

    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - anthropic is a core dep
            raise ProviderError(
                self.name,
                "the 'anthropic' package is not installed (pip install anthropic)",
                retryable=False,
            ) from exc

        availability = self.available()
        if not availability.available:
            raise ProviderError(self.name, availability.reason)

        model = model or self.config.model
        max_tokens = max_tokens or self.config.max_output_tokens
        client = anthropic.AsyncAnthropic(
            api_key=self._api_key(),
            timeout=float(self.config.llm_timeout_seconds),
        )

        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            **thinking_kwargs(model),
        }
        if system:
            request["system"] = system

        start = time.monotonic()
        try:
            if self._use_refusal_fallbacks(model):
                try:
                    message = await self._stream(
                        client.beta.messages,
                        {**request, "betas": [_FALLBACK_BETA], "fallbacks": "default"},
                    )
                except (TypeError, anthropic.BadRequestError) as exc:
                    # Older SDK or an endpoint without the beta: downgrade once
                    # for this process and retry plainly.
                    if not _mentions_fallbacks(exc):
                        raise
                    self._fallbacks_supported = False
                    message = await self._stream(client.messages, request)
            else:
                message = await self._stream(client.messages, request)
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                self.name, f"authentication failed: {exc.message}", retryable=False
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError(
                self.name, f"permission denied: {exc.message}", retryable=False
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(
                self.name, f"unknown model or endpoint: {exc.message}", retryable=False
            ) from exc
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "unknown")
            raise ProviderError(
                self.name, f"rate limited (retry-after: {retry_after}s)"
            ) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(
                self.name, f"bad request: {exc.message}", retryable=False
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                self.name,
                f"API error {exc.status_code}: {exc.message}",
                retryable=exc.status_code >= 500,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(self.name, f"connection error: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)

        if getattr(message, "stop_reason", None) == "refusal":
            detail = ""
            stop_details = getattr(message, "stop_details", None)
            if stop_details is not None:
                detail = f" ({getattr(stop_details, 'category', None) or 'unspecified'})"
            raise ProviderError(
                self.name, f"model declined the request{detail}", retryable=False
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        served_model = str(getattr(message, "model", model))

        return GenerationResult(
            text=text,
            provider=self.name,
            model=served_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(served_model, input_tokens, output_tokens),
            duration_ms=duration_ms,
            raw={"stop_reason": getattr(message, "stop_reason", None)},
        )

    @staticmethod
    async def _stream(endpoint: Any, request: dict[str, Any]) -> Any:
        """Stream a request and return the final message."""
        async with endpoint.stream(**request) as stream:
            return await stream.get_final_message()


def _mentions_fallbacks(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "fallback" in text or "beta" in text or "unexpected keyword" in text
