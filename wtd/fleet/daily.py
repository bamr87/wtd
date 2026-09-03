"""The daily harness: docs drift, Opus 5 pull-request review, merge gate.

The per-cycle scanners in :mod:`wtd.fleet.discovery` answer "what is broken
right now". This module answers the slower question — "what has quietly
gone stale since yesterday" — and runs the two passes that only make sense
once a day across the whole roster:

1. **Docs sweep** — for every repository, gather four cheap signals (README
   size, newest code commit, newest docs commit, commits since) and let
   :mod:`wtd.fleet.docsdrift` judge whether the documentation still
   describes the code. A verdict of *stale* becomes one ``write_docs`` work
   item per repo per UTC day.
2. **Review sweep** — queue every open pull request for the Opus 5
   ``reviewer`` role, keyed by *head commit* so a push earns a fresh review
   and an untouched branch does not burn tokens twice.
3. **Merge sweep** — for pull requests carrying a standing review approval,
   re-run the deterministic merge gate (:mod:`wtd.fleet.mergegate`) and
   merge the ones that are green. This is the pass that lets "CI went green
   overnight" turn into a merge without another model call.

The sweeps only ever *enqueue*; the orchestrator's normal dispatch loop
runs the agents, with all its existing budgets and guardrails. Merging is
the one new write, and it is gated four times over: apply mode, the
fleet-wide switch, the per-repo opt-in, and the gate itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from wtd.core.models import TodoPriority
from wtd.fleet.docsdrift import (
    DocsAssessment,
    DocsSignals,
    assess_docs,
    daily_anchor,
    utc_day,
)
from wtd.fleet.github import GitHubClient, GitHubError, has_marker
from wtd.fleet.mergegate import (
    APPROVAL_SHA_KEY,
    MergeDecision,
    MergePolicy,
    evaluate_merge,
    summarize_ci,
)
from wtd.fleet.models import WorkItem, WorkKind, WorkStatus, make_dedup_key
from wtd.fleet.settings import FleetSettings
from wtd.fleet.state import FleetState

logger = logging.getLogger(__name__)

#: How far back the "commits since the docs changed" count looks.
_COMMIT_SAMPLE = 100


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _commit_time(commit: dict[str, Any]) -> datetime | None:
    """The author/committer date of a commit payload."""
    detail = commit.get("commit") or {}
    for slot in ("committer", "author"):
        stamp = _parse_time((detail.get(slot) or {}).get("date"))
        if stamp is not None:
            return stamp
    return None


@dataclass
class DocsCheck:
    """One repository's docs verdict for the day."""

    repo: str
    assessment: DocsAssessment
    queued: bool = False
    error: str | None = None


@dataclass
class ReviewTarget:
    """One pull request the sweep considered for review."""

    repo: str
    number: int
    title: str
    head_sha: str
    draft: bool
    queued: bool = False
    reason: str = ""


@dataclass
class MergeAttempt:
    """One pull request the merge sweep evaluated."""

    repo: str
    number: int
    decision: MergeDecision
    merged: bool = False
    dry_run: bool = True
    error: str | None = None


@dataclass
class DailyReport:
    day: str = ""
    apply: bool = False
    docs: list[DocsCheck] = field(default_factory=list)
    reviews: list[ReviewTarget] = field(default_factory=list)
    merges: list[MergeAttempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def docs_queued(self) -> int:
        return sum(1 for check in self.docs if check.queued)

    @property
    def reviews_queued(self) -> int:
        return sum(1 for target in self.reviews if target.queued)

    @property
    def merged(self) -> int:
        return sum(1 for attempt in self.merges if attempt.merged)


class DailyHarness:
    """The three daily sweeps, over one roster."""

    def __init__(
        self,
        github: GitHubClient,
        settings: FleetSettings,
        state: FleetState,
        *,
        bot_marker: str = "wtd-fleet",
        now: datetime | None = None,
    ):
        self.github = github
        self.settings = settings
        self.state = state
        self.bot_marker = bot_marker
        self.now = now or datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    def _repos(self, repos: list[str] | None) -> list[str]:
        wanted = set(repos) if repos else None
        return [
            repo.slug
            for repo in self.settings.repos
            if wanted is None or repo.slug in wanted
        ]

    # ------------------------------------------------------------------
    # 1. Docs sweep
    # ------------------------------------------------------------------
    async def docs_signals(self, repo: str) -> DocsSignals:
        """Gather the four docs-freshness signals for one repository.

        Four small reads, no file downloads: the newest commit overall, the
        newest commit touching each documentation path, the commit count
        since then, and the README's size.
        """
        readme = await self.github.get_readme(repo)
        latest = await self.github.list_commits(repo, per_page=1)
        last_code = _commit_time(latest[0]) if latest else None

        last_docs: datetime | None = None
        present: list[str] = []
        for path in self.settings.daily.doc_paths:
            try:
                commits = await self.github.list_commits(repo, path=path, per_page=1)
            except GitHubError:
                continue
            if not commits:
                continue
            present.append(path)
            stamp = _commit_time(commits[0])
            if stamp is not None and (last_docs is None or stamp > last_docs):
                last_docs = stamp

        commits_since = 0
        if last_docs is not None:
            since = last_docs.isoformat().replace("+00:00", "Z")
            try:
                sample = await self.github.list_commits(
                    repo, since=since, per_page=_COMMIT_SAMPLE
                )
                # The commit at `since` is included by GitHub; don't count it.
                commits_since = max(0, len(sample) - 1)
            except GitHubError:
                commits_since = 0

        return DocsSignals(
            readme_chars=None if readme is None else len(readme),
            last_code_commit=last_code,
            last_docs_commit=last_docs,
            commits_since_docs=commits_since,
            doc_paths_present=tuple(present),
        )

    async def docs_sweep(self, repos: list[str] | None = None) -> list[DocsCheck]:
        """Assess every repository's docs, queueing one item per stale repo."""
        day = utc_day(self.now)
        policy = self.settings.daily.docs_policy()
        checks: list[DocsCheck] = []
        for slug in self._repos(repos):
            try:
                signals = await self.docs_signals(slug)
            except GitHubError as exc:
                checks.append(
                    DocsCheck(
                        repo=slug,
                        assessment=DocsAssessment(repo=slug),
                        error=str(exc)[:200],
                    )
                )
                continue
            assessment = assess_docs(slug, signals, policy)
            check = DocsCheck(repo=slug, assessment=assessment)
            if assessment.needs_update:
                check.queued = self.state.enqueue(
                    self._docs_item(slug, assessment, signals, day)
                )
            checks.append(check)
        return checks

    def _docs_item(
        self, repo: str, assessment: DocsAssessment, signals: DocsSignals, day: str
    ) -> WorkItem:
        return WorkItem(
            dedup_key=make_dedup_key(
                repo, WorkKind.WRITE_DOCS, daily_anchor(repo, day)
            ),
            kind=WorkKind.WRITE_DOCS,
            repo=repo,
            title=f"Daily docs check ({day}): update the docs for {repo}",
            description=(
                f"The daily docs sweep found drift: {assessment.summary}. "
                "Bring the documentation back in line with what the code now does."
            ),
            priority=assessment.priority,
            role_hint="doc-writer",
            discovered_by="scanner:docs-daily",
            evidence={
                "day": day,
                "reasons": assessment.reasons,
                "drift_days": assessment.drift_days,
                "commits_since_docs": assessment.commits_since_docs,
                "readme_chars": assessment.readme_chars,
                "doc_paths": list(signals.doc_paths_present),
            },
        )

    # ------------------------------------------------------------------
    # 2. Review sweep
    # ------------------------------------------------------------------
    async def review_sweep(self, repos: list[str] | None = None) -> list[ReviewTarget]:
        """Queue every open pull request for review, keyed by head commit."""
        targets: list[ReviewTarget] = []
        for slug in self._repos(repos):
            try:
                pulls = await self.github.list_pulls(slug, per_page=50)
            except GitHubError as exc:
                logger.info("daily review sweep: %s: %s", slug, exc)
                continue
            for pull in pulls:
                target = self._review_target(slug, pull)
                targets.append(target)
        return targets

    def _review_target(self, repo: str, pull: dict[str, Any]) -> ReviewTarget:
        number = int(pull.get("number") or 0)
        head_sha = str((pull.get("head") or {}).get("sha", ""))
        draft = bool(pull.get("draft"))
        target = ReviewTarget(
            repo=repo,
            number=number,
            title=str(pull.get("title", "")),
            head_sha=head_sha,
            draft=draft,
        )
        if draft and not self.settings.daily.review_drafts:
            target.reason = "draft (review_drafts is off)"
            return target
        if not head_sha:
            target.reason = "no head commit reported"
            return target

        # Keying on the head commit is the whole dedup story: the same
        # branch reviewed twice costs nothing, a new push costs one review.
        item = WorkItem(
            dedup_key=make_dedup_key(
                repo, WorkKind.REVIEW_PR, f"pr#{number}@{head_sha[:12]}"
            ),
            kind=WorkKind.REVIEW_PR,
            repo=repo,
            title=f"Review PR #{number}: {pull.get('title', '')}",
            description=str(pull.get("body") or "")[:800],
            url=pull.get("html_url"),
            priority=TodoPriority.HIGH,
            role_hint="reviewer",
            discovered_by="scanner:daily-review",
            evidence={
                "number": number,
                "head_sha": head_sha,
                "author": (pull.get("user") or {}).get("login", ""),
                "base": (pull.get("base") or {}).get("ref", ""),
                "head": (pull.get("head") or {}).get("ref", ""),
                "draft": draft,
                "labels": [str(lbl.get("name", "")) for lbl in pull.get("labels") or []],
                "fleet_authored": has_marker(str(pull.get("body") or ""), self.bot_marker),
            },
        )
        target.queued = self.state.enqueue(item)
        target.reason = "queued" if target.queued else "already queued for this head"
        return target

    # ------------------------------------------------------------------
    # 3. Merge sweep
    # ------------------------------------------------------------------
    def approvals(self, repos: list[str] | None = None) -> dict[tuple[str, int], str]:
        """Standing review approvals: ``(repo, pr number) -> approved sha``."""
        wanted = set(self._repos(repos))
        out: dict[tuple[str, int], str] = {}
        for item in self.state.items.values():
            if item.kind is not WorkKind.REVIEW_PR or item.repo not in wanted:
                continue
            sha = str(item.evidence.get(APPROVAL_SHA_KEY) or "")
            number = item.evidence.get("number")
            if sha and number:
                out[(item.repo, int(number))] = sha
        return out

    async def evaluate(
        self,
        repo: str,
        number: int,
        *,
        policy: MergePolicy,
        approved_sha: str | None,
    ) -> MergeDecision:
        """Run the merge gate against live GitHub state for one PR."""
        inputs = await self.github.merge_inputs(repo, number)
        pull = inputs["pull"]
        return evaluate_merge(
            pull,
            policy=policy,
            ci=summarize_ci(inputs["check_runs"], inputs["combined_status"]),
            reviews=inputs["reviews"],
            approved_sha=approved_sha,
            fleet_authored=has_marker(str(pull.get("body") or ""), self.bot_marker),
            repo=repo,
        )

    async def merge_sweep(
        self, repos: list[str] | None = None, *, apply: bool = False
    ) -> list[MergeAttempt]:
        """Merge the approved pull requests whose gate has since opened.

        Only pull requests with a standing approval are considered: the
        reviewer said yes, the gate says the facts still agree, and only
        then does anything merge.
        """
        attempts: list[MergeAttempt] = []
        approvals = self.approvals(repos)
        merged_this_sweep = 0
        for (slug, number), approved_sha in sorted(approvals.items()):
            policy = self.settings.merge_policy_for(slug)
            if not policy.enabled:
                continue
            if merged_this_sweep >= policy.max_per_cycle:
                break
            try:
                decision = await self.evaluate(
                    slug, number, policy=policy, approved_sha=approved_sha
                )
            except GitHubError as exc:
                logger.info("merge sweep: %s#%s: %s", slug, number, exc)
                continue
            attempt = MergeAttempt(
                repo=slug, number=number, decision=decision, dry_run=not apply
            )
            if decision.allowed and apply:
                try:
                    await self.github.merge_pull(
                        slug,
                        number,
                        sha=decision.head_sha,
                        method=decision.method,
                        commit_title=f"{decision.title} (#{number})",
                    )
                    attempt.merged = True
                    merged_this_sweep += 1
                    self._clear_approval(slug, number)
                except GitHubError as exc:
                    attempt.error = str(exc)[:300]
            attempts.append(attempt)
        return attempts

    def _clear_approval(self, repo: str, number: int) -> None:
        """Retire the standing approval once its pull request is merged."""
        for item in self.state.items.values():
            if (
                item.kind is WorkKind.REVIEW_PR
                and item.repo == repo
                and int(item.evidence.get("number") or 0) == number
            ):
                item.evidence.pop(APPROVAL_SHA_KEY, None)
                item.status = WorkStatus.DONE
                item.touch()

    # ------------------------------------------------------------------
    async def inspect_merges(
        self,
        repos: list[str] | None = None,
        *,
        require_approval: bool | None = None,
    ) -> list[MergeAttempt]:
        """Read-only: evaluate the gate for every open PR. Never merges.

        This is the operator's window into the gate — `wtd fleet
        merge-check`. ``require_approval=False`` answers "would CI and
        policy allow this?" without waiting for a reviewer to have run.
        """
        approvals = self.approvals(repos)
        attempts: list[MergeAttempt] = []
        for slug in self._repos(repos):
            policy = self.settings.merge_policy_for(slug)
            if require_approval is False:
                policy = MergePolicy(
                    enabled=policy.enabled,
                    method=policy.method,
                    require_checks=policy.require_checks,
                    require_review_approval=False,
                    allow_fleet_authored=policy.allow_fleet_authored,
                    blocked_labels=policy.blocked_labels,
                    max_per_cycle=policy.max_per_cycle,
                )
            try:
                pulls = await self.github.list_pulls(slug, per_page=50)
            except GitHubError as exc:
                logger.info("merge-check: %s: %s", slug, exc)
                continue
            for pull in pulls:
                number = int(pull.get("number") or 0)
                try:
                    decision = await self.evaluate(
                        slug,
                        number,
                        policy=policy,
                        approved_sha=approvals.get((slug, number)),
                    )
                except GitHubError as exc:
                    logger.info("merge-check: %s#%s: %s", slug, number, exc)
                    continue
                attempts.append(
                    MergeAttempt(repo=slug, number=number, decision=decision)
                )
        return attempts


async def gather_daily(
    harness: DailyHarness,
    *,
    repos: list[str] | None = None,
    docs: bool = True,
    review: bool = True,
) -> tuple[list[DocsCheck], list[ReviewTarget]]:
    """Run the two discovery sweeps concurrently."""
    docs_task = harness.docs_sweep(repos) if docs else _empty()
    review_task = harness.review_sweep(repos) if review else _empty()
    docs_result, review_result = await asyncio.gather(docs_task, review_task)
    return list(docs_result), list(review_result)


async def _empty() -> list:
    return []
