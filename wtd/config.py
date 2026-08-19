"""
WTD Configuration Management

WTD is Claude-first: the default provider chain is Claude Code OAuth
(subscription) with the Anthropic API as fallback. Secrets are read from
the environment (both ``WTD_``-prefixed and the conventional unprefixed
names), never from checked-in files.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class WTDConfig(BaseSettings):
    """WTD Configuration with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="WTD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM / provider settings
    # ------------------------------------------------------------------
    llm_provider: Literal["auto", "claude-code", "anthropic", "ollama", "openai"] = Field(
        default="auto",
        description=(
            "Provider selection. 'auto' (default) resolves the chain "
            "Claude Code OAuth -> Anthropic API. Explicit names disable "
            "failover."
        ),
    )
    model: str = Field(
        default="claude-opus-5",
        description=(
            "Default model. Full API model ID; the Claude Code lane maps it "
            "to the matching CLI alias (e.g. claude-opus-5 -> opus)."
        ),
    )
    max_output_tokens: int = Field(
        default=16000,
        ge=256,
        le=128000,
        description="Default max output tokens per generation.",
    )
    llm_timeout_seconds: int = Field(
        default=600,
        ge=30,
        le=3600,
        description="Timeout for a single LLM call (either lane).",
    )

    # Claude Code lane (default)
    claude_code_oauth_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WTD_CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"
        ),
        description=(
            "Claude Code OAuth token (from `claude setup-token`). Optional "
            "on machines with an interactive `claude` login."
        ),
    )
    claude_cli_path: str = Field(
        default="claude",
        description="Path or name of the claude CLI binary.",
    )

    # Anthropic API lane (fallback)
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WTD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        description="Anthropic API key for the fallback lane.",
    )
    anthropic_refusal_fallbacks: bool = Field(
        default=True,
        description=(
            "Enable server-side refusal fallbacks (fallbacks='default') on "
            "Opus 5 / Fable 5 requests to the Anthropic API."
        ),
    )

    # Legacy providers (explicit opt-in only)
    ollama_model: str = Field(default="llama3.2", description="Ollama model name")
    ollama_host: str = Field(
        default="http://localhost:11434", description="Ollama API host"
    )
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(
        default="gpt-4-turbo-preview", description="OpenAI model name"
    )

    # ------------------------------------------------------------------
    # GitHub (fleet work discovery + actions)
    # ------------------------------------------------------------------
    github_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WTD_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"),
        description="GitHub token for fleet discovery and (with apply) writes.",
    )
    github_api_url: str = Field(
        default="https://api.github.com",
        description="GitHub REST API base URL (override for GHES).",
    )

    # ------------------------------------------------------------------
    # Fleet orchestration
    # ------------------------------------------------------------------
    fleet_enabled: bool = Field(
        default=True,
        description="Master kill switch for the fleet subsystem.",
    )
    fleet_apply: bool = Field(
        default=False,
        description=(
            "When false (default) fleet runs are dry-run: agents think, but "
            "nothing is written to GitHub. Set true (or pass --apply) to act."
        ),
    )
    fleet_repos: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Comma-separated owner/repo roster. Usually set in wtd.yml "
            "instead; this env form is for one-off runs."
        ),
    )
    fleet_config: Path | None = Field(
        default=None,
        description="Path to wtd.yml (default: ./wtd.yml then ~/.wtd/wtd.yml).",
    )
    fleet_state_dir: Path | None = Field(
        default=None,
        description="Fleet state directory (queue, ledger). Default: <config_dir>/fleet.",
    )
    fleet_interval_seconds: int = Field(
        default=900,
        ge=60,
        description="Sleep between orchestrator cycles in `wtd fleet loop`.",
    )
    fleet_concurrency: int = Field(
        default=2,
        ge=1,
        le=16,
        description="Concurrent agent runs per cycle.",
    )
    fleet_max_runs_per_cycle: int = Field(
        default=8,
        ge=1,
        description="Cap on agent runs per orchestrator cycle.",
    )
    fleet_max_writes_per_cycle: int = Field(
        default=5,
        ge=0,
        description="Cap on GitHub write actions per cycle (apply mode).",
    )
    fleet_claude_code_daily_tokens: int = Field(
        default=1_500_000,
        ge=0,
        description="Daily token budget for the Claude Code (subscription) lane.",
    )
    fleet_anthropic_daily_tokens: int = Field(
        default=500_000,
        ge=0,
        description="Daily token budget for the Anthropic API lane.",
    )
    fleet_anthropic_daily_usd: float = Field(
        default=10.0,
        ge=0.0,
        description="Daily estimated-spend cap (USD) for the Anthropic API lane.",
    )
    bot_marker: str = Field(
        default="wtd-fleet",
        description=(
            "Marker embedded in everything the fleet writes to GitHub; used "
            "for dedup and self-loop guards."
        ),
    )

    # ------------------------------------------------------------------
    # Recursion settings (local TODO tree)
    # ------------------------------------------------------------------
    max_recursion_depth: int = Field(
        default=7,
        ge=1,
        le=10,
        description="Maximum TODO recursion depth",
    )
    fitness_decay_rate: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Fitness decay per recursion level",
    )

    # ------------------------------------------------------------------
    # Execution settings
    # ------------------------------------------------------------------
    auto_execute: bool = Field(
        default=False,
        description="Auto-execute generated tasks without confirmation",
    )
    timeout_seconds: int = Field(
        default=90,
        ge=10,
        le=600,
        description="Timeout for first action (KFI target: 90s)",
    )

    # ------------------------------------------------------------------
    # Storage settings
    # ------------------------------------------------------------------
    db_path: Path = Field(
        default=Path.home() / ".wtd" / "wtd.db",
        description="SQLite database path",
    )
    config_dir: Path = Field(
        default=Path.home() / ".wtd",
        description="Configuration directory",
    )

    # ------------------------------------------------------------------
    # UI settings
    # ------------------------------------------------------------------
    theme: Literal["dark", "light", "auto"] = Field(
        default="dark",
        description="Terminal UI theme",
    )
    show_timestamps: bool = Field(
        default=True,
        description="Show timestamps in output",
    )

    # ------------------------------------------------------------------
    # API / server settings
    # ------------------------------------------------------------------
    api_host: str = Field(
        default="127.0.0.1",
        description=(
            "Host the WTD API server binds to. Defaults to localhost so the "
            "REST API (which can trigger workspace actions) is not exposed "
            "on the network. Override only if you know what you're doing."
        ),
    )
    api_port: int = Field(
        default=8787,
        ge=1,
        le=65535,
        description="Port the WTD API server binds to.",
    )
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Allowed CORS origins for the WTD API. Empty (default) means "
            "no cross-origin requests are accepted, which is appropriate "
            "for the local-first default. Set a comma-separated list via "
            "WTD_API_CORS_ORIGINS to opt in (e.g. 'http://localhost:3000')."
        ),
    )

    def ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fleet_state_path.mkdir(parents=True, exist_ok=True)

    @property
    def fleet_state_path(self) -> Path:
        """The fleet state directory, always resolved to a concrete path."""
        return self.fleet_state_dir or (self.config_dir / "fleet")

    @model_validator(mode="after")
    def _default_fleet_state_dir(self) -> "WTDConfig":
        if self.fleet_state_dir is None:
            self.fleet_state_dir = self.config_dir / "fleet"
        return self

    @field_validator("api_cors_origins", "fleet_repos", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated values from env vars (in addition to JSON)."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            # Accept JSON-encoded lists for parity with pydantic-settings.
            if stripped.startswith("["):
                import json

                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    # Fall through to comma-splitting on malformed JSON.
                    pass
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


# Global config instance
_config: WTDConfig | None = None


def get_config() -> WTDConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = WTDConfig()
        _config.ensure_dirs()
    return _config


def reset_config() -> None:
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None
