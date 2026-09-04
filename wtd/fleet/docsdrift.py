"""Docs drift: does this repository's documentation still describe it?

The original docs scanner asked one question — "is there a README, and is
it more than a stub?" — which only ever fires once per repository. A daily
sweep needs a question that can go from *no* to *yes* as a repo evolves:
**have the docs kept up with the code?**

The signals are cheap and deterministic (four small GitHub reads per repo,
gathered by the caller): when code last changed, when documentation last
changed, how many commits have landed since, and how big the README is.
This module turns those into a verdict with its reasons attached, so the
work item the fleet queues says *why* the docs look stale instead of
asking an agent to guess.

Pure: no I/O, clock injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from wtd.core.models import TodoPriority

#: Paths whose commits count as "documentation was updated".
DEFAULT_DOC_PATHS = ("README.md", "docs")


@dataclass(frozen=True)
class DocsPolicy:
    """Thresholds for calling documentation stale."""

    min_readme_chars: int = 300
    #: Docs must lag code by at least this many days to count as drift.
    stale_after_days: int = 14
    #: …and by at least this many commits, so a quiet repo is not nagged.
    min_commits_since_docs: int = 5
    doc_paths: tuple[str, ...] = DEFAULT_DOC_PATHS


@dataclass
class DocsSignals:
    """What the caller observed about one repository."""

    readme_chars: int | None = None  # None = the repo has no README
    last_code_commit: datetime | None = None
    last_docs_commit: datetime | None = None
    #: Commits on the default branch since ``last_docs_commit`` (capped).
    commits_since_docs: int = 0
    #: Documentation paths that actually exist in the repo.
    doc_paths_present: tuple[str, ...] = ()


@dataclass
class DocsAssessment:
    """The verdict for one repository on one day."""

    repo: str
    needs_update: bool = False
    priority: TodoPriority = TodoPriority.MEDIUM
    reasons: list[str] = field(default_factory=list)
    drift_days: float = 0.0
    commits_since_docs: int = 0
    readme_chars: int | None = None

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "documentation looks current"


def _days_between(later: datetime | None, earlier: datetime | None) -> float:
    if later is None or earlier is None:
        return 0.0
    if later.tzinfo is None:
        later = later.replace(tzinfo=timezone.utc)
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    return max(0.0, (later - earlier).total_seconds() / 86400)


def assess_docs(
    repo: str,
    signals: DocsSignals,
    policy: DocsPolicy | None = None,
) -> DocsAssessment:
    """Decide whether ``repo`` needs a documentation pass, and say why."""
    policy = policy or DocsPolicy()
    drift_days = _days_between(signals.last_code_commit, signals.last_docs_commit)
    assessment = DocsAssessment(
        repo=repo,
        drift_days=round(drift_days, 1),
        commits_since_docs=signals.commits_since_docs,
        readme_chars=signals.readme_chars,
    )

    if signals.readme_chars is None:
        assessment.needs_update = True
        assessment.priority = TodoPriority.HIGH
        assessment.reasons.append("the repository has no README")
        return assessment

    if signals.readme_chars < policy.min_readme_chars:
        assessment.needs_update = True
        assessment.reasons.append(
            f"the README is only {signals.readme_chars} characters "
            f"(under {policy.min_readme_chars})"
        )

    if signals.last_code_commit is not None and signals.last_docs_commit is None:
        assessment.needs_update = True
        assessment.reasons.append(
            "no commit in the sampled history touched "
            f"{' or '.join(policy.doc_paths)}"
        )
    elif (
        drift_days >= policy.stale_after_days
        and signals.commits_since_docs >= policy.min_commits_since_docs
    ):
        assessment.needs_update = True
        assessment.reasons.append(
            f"docs last changed {assessment.drift_days:.0f} days and "
            f"{signals.commits_since_docs} commits before the newest code commit"
        )

    return assessment


def daily_anchor(repo: str, day: str) -> str:
    """Dedup anchor for one repository's docs check on one UTC day.

    Including the day is what makes the check *daily*: yesterday's item is
    a different work item, so a repo that stays stale is re-queued once a
    day rather than once ever — and a repo checked twice in one day is not
    queued twice.
    """
    return f"docs-daily:{repo}:{day}"


def utc_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
