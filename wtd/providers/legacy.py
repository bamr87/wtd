"""Legacy providers: Ollama and OpenAI.

These predate the platform redesign. They are kept for users who select
them explicitly (``WTD_LLM_PROVIDER=ollama|openai``) but are never part of
the automatic Claude Code → Anthropic chain, and the fleet's capacity
balancer does not model them as lanes.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from wtd.providers.base import (
    Availability,
    GenerationResult,
    LLMProvider,
    ProviderError,
)

if TYPE_CHECKING:
    from wtd.config import WTDConfig


class OllamaProvider(LLMProvider):
    """Local models via Ollama (explicit opt-in only)."""

    name = "ollama"

    def __init__(self, config: "WTDConfig"):
        self.config = config

    def available(self) -> Availability:
        try:
            import ollama  # noqa: F401
        except ImportError:
            return Availability(
                False, "the 'ollama' package is not installed (pip install 'wtd[ollama]')"
            )
        return Availability(True, f"ollama host {self.config.ollama_host}")

    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,  # noqa: ARG002 - not supported by chat API
    ) -> GenerationResult:
        availability = self.available()
        if not availability.available:
            raise ProviderError(self.name, availability.reason, retryable=False)

        import ollama

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        model = model or self.config.ollama_model
        start = time.monotonic()
        try:
            response = await asyncio.to_thread(
                ollama.chat, model=model, messages=messages
            )
        except Exception as exc:
            raise ProviderError(self.name, f"ollama call failed: {exc}") from exc

        return GenerationResult(
            text=response["message"]["content"],
            provider=self.name,
            model=model,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


class OpenAIProvider(LLMProvider):
    """OpenAI chat completions (explicit opt-in only)."""

    name = "openai"

    def __init__(self, config: "WTDConfig"):
        self.config = config

    def available(self) -> Availability:
        try:
            import openai  # noqa: F401
        except ImportError:
            return Availability(
                False, "the 'openai' package is not installed (pip install 'wtd[openai]')"
            )
        if not self.config.openai_api_key:
            return Availability(False, "WTD_OPENAI_API_KEY is not set")
        return Availability(True, "openai key configured")

    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        availability = self.available()
        if not availability.available:
            raise ProviderError(self.name, availability.reason, retryable=False)

        from typing import Any

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.config.openai_api_key)
        messages: list[Any] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        model = model or self.config.openai_model
        start = time.monotonic()
        try:
            if max_tokens:
                response = await client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens
                )
            else:
                response = await client.chat.completions.create(
                    model=model, messages=messages
                )
        except Exception as exc:
            raise ProviderError(self.name, f"openai call failed: {exc}") from exc

        usage = getattr(response, "usage", None)
        return GenerationResult(
            text=response.choices[0].message.content or "",
            provider=self.name,
            model=model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
