"""
WTD Configuration Management
"""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


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

