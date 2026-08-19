"""Fleet settings: the wtd.yml roster + tunables.

Environment config (:class:`wtd.config.WTDConfig`) carries secrets and
switches; ``wtd.yml`` carries the fleet's shape — which repos to watch,
which roles run where, budgets and caps. Env values act as defaults that
wtd.yml can override, so a repo-committed wtd.yml fully describes a fleet.

Search order for the file: ``WTD_FLEET_CONFIG`` → ``./wtd.yml`` →
``~/.wtd/wtd.yml``. No file at all is fine — the roster can come from
``WTD_FLEET_REPOS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wtd.config import WTDConfig, get_config

_REPO_SLUG_ERROR = "fleet repo entries must be 'owner/name' slugs, got: {value!r}"


@dataclass
class RepoConfig:
    """One roster entry."""

    slug: str  # "owner/name"
    roles: list[str] = field(default_factory=list)  # empty = all enabled roles
    articles: bool = False  # opt-in for the author role's cadence

    @property
    def owner(self) -> str:
        return self.slug.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.slug.split("/", 1)[1]


@dataclass
class ScanConfig:
    issues: bool = True
    pulls: bool = True
    ci: bool = True
    docs: bool = True
    local_todos: bool = False


@dataclass
class FleetSettings:
    repos: list[RepoConfig] = field(default_factory=list)
    roles_enabled: list[str] = field(default_factory=list)  # empty = all built-ins
    scan: ScanConfig = field(default_factory=ScanConfig)
    max_runs_per_cycle: int = 8
    max_writes_per_cycle: int = 5
    max_discovered_per_run: int = 5
    max_attempts: int = 3
    stale_after_days: int = 14
    claude_code_daily_tokens: int = 1_500_000
    anthropic_daily_tokens: int = 500_000
    anthropic_daily_usd: float = 10.0
    source_path: Path | None = None

    def repo_slugs(self) -> list[str]:
        return [r.slug for r in self.repos]

    def repo(self, slug: str) -> RepoConfig | None:
        for repo in self.repos:
            if repo.slug == slug:
                return repo
        return None


def _parse_repo_entry(entry: Any) -> RepoConfig:
    if isinstance(entry, str):
        slug = entry.strip()
        extra: dict[str, Any] = {}
    elif isinstance(entry, dict):
        slug = str(entry.get("repo") or entry.get("slug") or "").strip()
        extra = entry
    else:
        raise ValueError(_REPO_SLUG_ERROR.format(value=entry))

    if slug.count("/") != 1 or not all(part.strip() for part in slug.split("/")):
        raise ValueError(_REPO_SLUG_ERROR.format(value=slug))

    roles = extra.get("roles") or []
    if not isinstance(roles, list):
        raise ValueError(f"roles for {slug} must be a list, got {roles!r}")
    return RepoConfig(
        slug=slug,
        roles=[str(r) for r in roles],
        articles=bool(extra.get("articles", False)),
    )


def find_settings_path(config: WTDConfig) -> Path | None:
    if config.fleet_config is not None:
        return config.fleet_config
    for candidate in (Path.cwd() / "wtd.yml", config.config_dir / "wtd.yml"):
        if candidate.is_file():
            return candidate
    return None


def load_settings(config: WTDConfig | None = None) -> FleetSettings:
    """Build FleetSettings from wtd.yml layered over env defaults."""
    config = config or get_config()

    settings = FleetSettings(
        repos=[_parse_repo_entry(slug) for slug in config.fleet_repos],
        max_runs_per_cycle=config.fleet_max_runs_per_cycle,
        max_writes_per_cycle=config.fleet_max_writes_per_cycle,
        claude_code_daily_tokens=config.fleet_claude_code_daily_tokens,
        anthropic_daily_tokens=config.fleet_anthropic_daily_tokens,
        anthropic_daily_usd=config.fleet_anthropic_daily_usd,
    )

    path = find_settings_path(config)
    if path is None:
        return settings
    if not path.is_file():
        raise FileNotFoundError(f"fleet config not found: {path}")

    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    fleet = raw.get("fleet", raw)
    if not isinstance(fleet, dict):
        raise ValueError(f"the 'fleet' section of {path} must be a mapping")

    settings.source_path = path

    if "repos" in fleet:
        settings.repos = [_parse_repo_entry(e) for e in (fleet.get("repos") or [])]
    if "roles_enabled" in fleet:
        settings.roles_enabled = [str(r) for r in (fleet.get("roles_enabled") or [])]

    scan = fleet.get("scan") or {}
    if isinstance(scan, dict):
        for key in ("issues", "pulls", "ci", "docs", "local_todos"):
            if key in scan:
                setattr(settings.scan, key, bool(scan[key]))

    for key in (
        "max_runs_per_cycle",
        "max_writes_per_cycle",
        "max_discovered_per_run",
        "max_attempts",
        "stale_after_days",
    ):
        if key in fleet:
            setattr(settings, key, int(fleet[key]))

    budgets = fleet.get("budgets") or {}
    if isinstance(budgets, dict):
        if "claude_code_daily_tokens" in budgets:
            settings.claude_code_daily_tokens = int(budgets["claude_code_daily_tokens"])
        if "anthropic_daily_tokens" in budgets:
            settings.anthropic_daily_tokens = int(budgets["anthropic_daily_tokens"])
        if "anthropic_daily_usd" in budgets:
            settings.anthropic_daily_usd = float(budgets["anthropic_daily_usd"])

    return settings
