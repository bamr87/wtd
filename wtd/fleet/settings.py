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

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wtd.config import WTDConfig, get_config
from wtd.fleet.docsdrift import DEFAULT_DOC_PATHS, DocsPolicy
from wtd.fleet.mergegate import (
    DEFAULT_BLOCKED_LABELS,
    MERGE_METHODS,
    MergePolicy,
)

_REPO_SLUG_ERROR = "fleet repo entries must be 'owner/name' slugs, got: {value!r}"

#: The merge gate's environment switch. wtd.yml layers over env everywhere
#: else, but a kill switch may only ever NARROW: an explicit
#: ``WTD_FLEET_MERGE_ENABLED=false`` wins over a committed
#: ``fleet.merge.enabled: true``, so an operator can stop merging without
#: editing (or being able to edit) the repository's config.
MERGE_ENV_VAR = "WTD_FLEET_MERGE_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def env_forbids_merging() -> bool:
    """True when the environment explicitly turns the merge gate off."""
    raw = os.environ.get(MERGE_ENV_VAR)
    return raw is not None and raw.strip().lower() not in _TRUTHY


@dataclass
class RepoConfig:
    """One roster entry."""

    slug: str  # "owner/name"
    roles: list[str] = field(default_factory=list)  # empty = all enabled roles
    articles: bool = False  # opt-in for the author role's cadence
    #: Per-repo opt-in for the merge gate. Off unless a human writes it down.
    merge: bool = False

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
class DailyConfig:
    """The once-a-day sweep: docs drift across the roster, PR review, merge.

    Separate from :class:`ScanConfig` because the cadence is the point.
    The per-cycle scanners answer "what is broken right now"; the daily
    sweep answers "what has quietly gone stale since yesterday".
    """

    docs: bool = True
    review: bool = True
    #: Review draft pull requests too. The per-cycle scanner skips drafts
    #: (they are works in progress); the daily sweep looks at them because
    #: early feedback is cheaper than late feedback. Drafts are never
    #: merged — the gate refuses them.
    review_drafts: bool = True
    #: Docs-drift thresholds (see :mod:`wtd.fleet.docsdrift`).
    stale_after_days: int = 14
    min_commits_since_docs: int = 5
    doc_paths: list[str] = field(default_factory=lambda: list(DEFAULT_DOC_PATHS))

    def docs_policy(self, min_readme_chars: int = 300) -> DocsPolicy:
        return DocsPolicy(
            min_readme_chars=min_readme_chars,
            stale_after_days=self.stale_after_days,
            min_commits_since_docs=self.min_commits_since_docs,
            doc_paths=tuple(self.doc_paths),
        )


@dataclass
class MergeConfig:
    """Fleet-wide merge policy. Every field is a lock, all default shut."""

    enabled: bool = False
    method: str = "squash"
    require_checks: bool = True
    require_review_approval: bool = True
    #: Merging the fleet's OWN pull requests. The house convention says an
    #: agent never merges its own work; turning this on for a repository is
    #: a deliberate, documented exception, not a default.
    allow_fleet_authored: bool = False
    blocked_labels: list[str] = field(
        default_factory=lambda: list(DEFAULT_BLOCKED_LABELS)
    )
    max_per_cycle: int = 2


@dataclass
class FleetSettings:
    repos: list[RepoConfig] = field(default_factory=list)
    roles_enabled: list[str] = field(default_factory=list)  # empty = all built-ins
    scan: ScanConfig = field(default_factory=ScanConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)
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

    def repo_role_allowlist(self) -> dict[str, list[str]]:
        """Per-repo role allowlists, for the repos that declare one.

        A roster entry's ``roles: [...]`` is an allowlist: only those
        agents touch that repository. An empty list means "every enabled
        role", so such repos are simply absent from the mapping and the
        scheduler leaves them unnarrowed.
        """
        return {repo.slug: list(repo.roles) for repo in self.repos if repo.roles}

    def merge_policy_for(self, slug: str) -> MergePolicy:
        """The merge policy in force for one repository.

        Two independent switches must agree before a merge is even
        considered: the fleet-wide ``merge.enabled`` and the repository's
        own ``merge: true``. Apply mode is the third, enforced by the
        dispatcher, and the gate itself is the fourth.
        """
        repo = self.repo(slug)
        return MergePolicy(
            enabled=self.merge.enabled and bool(repo and repo.merge),
            method=self.merge.method,
            require_checks=self.merge.require_checks,
            require_review_approval=self.merge.require_review_approval,
            allow_fleet_authored=self.merge.allow_fleet_authored,
            blocked_labels=tuple(self.merge.blocked_labels),
            max_per_cycle=self.merge.max_per_cycle,
        )


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
        merge=bool(extra.get("merge", False)),
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
        merge=MergeConfig(enabled=config.fleet_merge_enabled),
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

    daily = fleet.get("daily") or {}
    if isinstance(daily, dict):
        for key in ("docs", "review", "review_drafts"):
            if key in daily:
                setattr(settings.daily, key, bool(daily[key]))
        for key in ("stale_after_days", "min_commits_since_docs"):
            if key in daily:
                setattr(settings.daily, key, int(daily[key]))
        if "doc_paths" in daily:
            settings.daily.doc_paths = [str(p) for p in (daily.get("doc_paths") or [])]

    merge = fleet.get("merge") or {}
    if isinstance(merge, dict):
        for key in (
            "enabled",
            "require_checks",
            "require_review_approval",
            "allow_fleet_authored",
        ):
            if key in merge:
                setattr(settings.merge, key, bool(merge[key]))
        if "method" in merge:
            method = str(merge["method"])
            if method not in MERGE_METHODS:
                raise ValueError(
                    f"fleet.merge.method must be one of {MERGE_METHODS}, got {method!r}"
                )
            settings.merge.method = method
        if "max_per_cycle" in merge:
            settings.merge.max_per_cycle = int(merge["max_per_cycle"])
        if "blocked_labels" in merge:
            settings.merge.blocked_labels = [
                str(label) for label in (merge.get("blocked_labels") or [])
            ]

    if settings.merge.enabled and env_forbids_merging():
        settings.merge.enabled = False

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
