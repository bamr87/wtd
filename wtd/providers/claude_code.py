"""Claude Code headless provider — the default WTD lane.

Runs the ``claude`` CLI in print mode (``claude -p --output-format json``)
with all built-in tools disabled, so it behaves as a pure text-generation
endpoint powered by the user's Claude subscription.

Authentication, in order of precedence:

1. ``CLAUDE_CODE_OAUTH_TOKEN`` (or ``WTD_CLAUDE_CODE_OAUTH_TOKEN``) — the
   long-lived token minted by ``claude setup-token``. This is the same
   convention the fleet's GitHub Actions use.
2. An existing interactive ``claude`` login on this machine.

When the OAuth token is present, ``ANTHROPIC_API_KEY`` is removed from the
child environment so this lane always bills the subscription, never the
metered API — that separation is what makes lane-level load balancing
meaningful.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from wtd.providers.base import (
    Availability,
    GenerationResult,
    LLMProvider,
    ProviderError,
)

if TYPE_CHECKING:
    from wtd.config import WTDConfig

# The claude CLI accepts aliases as well as full model IDs. Aliases survive
# model-name rollovers on the subscription side, so map the common full IDs
# down to their alias for this lane.
_MODEL_ALIASES = {
    "claude-fable-5": "fable",
    "claude-opus-5": "opus",
    "claude-opus-4-8": "opus",
    "claude-opus-4-7": "opus",
    "claude-opus-4-6": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
}


def cli_model_alias(model: str) -> str:
    """Map a full model ID to the claude CLI alias for that family."""
    return _MODEL_ALIASES.get(model, model)


class ClaudeCodeProvider(LLMProvider):
    """Generate text through a headless Claude Code invocation."""

    name = "claude-code"

    def __init__(self, config: "WTDConfig"):
        self.config = config

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    def _cli_path(self) -> str | None:
        """Resolve the claude CLI binary, honouring config overrides."""
        candidate = self.config.claude_cli_path or "claude"
        if os.path.sep in candidate:
            return candidate if os.access(candidate, os.X_OK) else None
        return shutil.which(candidate)

    def _oauth_token(self) -> str | None:
        return self.config.claude_code_oauth_token or os.environ.get(
            "CLAUDE_CODE_OAUTH_TOKEN"
        )

    def available(self) -> Availability:
        cli = self._cli_path()
        if cli is None:
            return Availability(
                False,
                "claude CLI not found (install: https://claude.com/claude-code, "
                "or set WTD_CLAUDE_CLI_PATH)",
            )
        if self._oauth_token():
            return Availability(True, f"claude CLI at {cli} with OAuth token")
        return Availability(
            True,
            f"claude CLI at {cli} (no OAuth token set; relies on local login)",
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def _build_command(self, system: str, model: str) -> list[str]:
        cli = self._cli_path()
        if cli is None:  # pragma: no cover - guarded by available()
            raise ProviderError(self.name, "claude CLI not found", retryable=True)
        cmd = [
            cli,
            "-p",
            "--output-format",
            "json",
            # Pure text generation: no built-in tools, no session files, and
            # no user/project settings bleeding into fleet runs.
            "--tools",
            "",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--model",
            cli_model_alias(model),
        ]
        if system:
            cmd += ["--system-prompt", system]
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        token = self._oauth_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            # Keep the subscription lane pure: never silently bill the API key.
            env.pop("ANTHROPIC_API_KEY", None)
        # Non-interactive environments should never trigger telemetry prompts.
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        return env

    async def generate(
        self,
        prompt: str,
        system: str = "",
        *,
        model: str | None = None,
        max_tokens: int | None = None,  # noqa: ARG002 - CLI manages output size
    ) -> GenerationResult:
        model = model or self.config.model
        cmd = self._build_command(system, model)
        timeout = self.config.llm_timeout_seconds

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
            )
        except OSError as exc:
            raise ProviderError(self.name, f"failed to launch claude CLI: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ProviderError(
                self.name, f"timed out after {timeout}s", retryable=True
            ) from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
            raise ProviderError(
                self.name,
                f"claude CLI exited with {proc.returncode}: {detail or 'no stderr'}",
            )

        payload = self._parse_output(stdout.decode("utf-8", errors="replace"))
        usage = payload.get("usage") or {}
        return GenerationResult(
            text=str(payload.get("result", "")),
            provider=self.name,
            model=str(payload.get("model") or model),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(payload.get("total_cost_usd") or 0.0),
            duration_ms=duration_ms,
            raw=payload,
        )

    def _parse_output(self, raw: str) -> dict[str, Any]:
        """Parse the CLI's ``--output-format json`` payload."""
        raw = raw.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                self.name, f"unparseable CLI output: {raw[:500]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError(self.name, "unexpected CLI output shape")
        if payload.get("is_error"):
            raise ProviderError(
                self.name,
                f"claude reported an error: {str(payload.get('result'))[:1000]}",
            )
        return payload
