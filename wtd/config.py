"""
WTD Configuration Management
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class WTDConfig(BaseSettings):
    """WTD Configuration with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="WTD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Settings
    llm_provider: Literal["ollama", "openai", "anthropic"] = Field(
        default="ollama",
        description="LLM provider to use",
    )
    ollama_model: str = Field(
        default="llama3.2",
        description="Ollama model name",
    )
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama API host",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key",
    )
    openai_model: str = Field(
        default="gpt-4-turbo-preview",
        description="OpenAI model name",
    )
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key",
    )
    anthropic_model: str = Field(
        default="claude-3-opus-20240229",
        description="Anthropic model name",
    )

    # Recursion Settings
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

    # Execution Settings
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

    # Storage Settings
    db_path: Path = Field(
        default=Path.home() / ".wtd" / "wtd.db",
        description="SQLite database path",
    )
    config_dir: Path = Field(
        default=Path.home() / ".wtd",
        description="Configuration directory",
    )

    # UI Settings
    theme: Literal["dark", "light", "auto"] = Field(
        default="dark",
        description="Terminal UI theme",
    )
    show_timestamps: bool = Field(
        default=True,
        description="Show timestamps in output",
    )

    # API / Server Settings
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

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
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
