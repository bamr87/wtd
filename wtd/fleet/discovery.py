"""Work discovery: turn repository signals into queued work items.

Each scanner is deterministic — no model calls — and produces
:class:`WorkItem` objects with stable dedup keys, so repeated scans
converge instead of duplicating. Agents add a second discovery channel at
dispatch time (their ``discovered`` output); both funnel through
:meth:`FleetState.enqueue`.

Loop guards live here: items authored by bots or by the fleet's own
GitHub login are never queued, and items the fleet has already answered
(marker comment present) are filtered by the dispatcher before acting.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from wtd.core.models import TodoPriority
from wtd.fleet.github import GitHubClient, GitHubError
from wtd.fleet.models import WorkItem, WorkKind, make_dedup_key
from wtd.fleet.settings import FleetSettings, RepoConfig

_EXCERPT_LEN = 800


def _excerpt(text: str | None, limit: int = _EXCERPT_LEN) -> str:
    text = (text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _is_bot(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    if user.get("type") == "Bot":
        return True
    return str(user.get("login", "")).endswith("[bot]")


def _author_is_self(user: dict[str, Any] | None, self_login: str | None) -> bool:
    if not self_login or not user:
        return False
    return user.get("login") == self_login


def _age_days(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


class RepoDiscovery:
    """Run the enabled scanners against one repository."""

    def __init__(
        self,
        client: GitHubClient,
        repo: RepoConfig,
        settings: FleetSettings,
        self_login: str | None = None,
    ):
        self.client = client
        self.repo = repo
        self.settings = settings
        self.self_login = self_login

    async def discover(self) -> list[WorkItem]:
        scanners = []
        if self.settings.scan.issues:
            scanners.append(self.scan_issues())
        if self.settings.scan.pulls:
            scanners.append(self.scan_pulls())
        if self.settings.scan.ci:
            scanners.append(self.scan_ci())
        if self.settings.scan.docs:
            scanners.append(self.scan_docs())
        if self.repo.articles:
            scanners.append(self.scan_article_cadence())

        items: list[WorkItem] = []
        results = await asyncio.gather(*scanners, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                # One failing signal (e.g. Actions disabled) must not sink
                # the rest of discovery for the repo.
                if isinstance(result, GitHubError):
                    continue
                raise result
            items.extend(result)
        return items

    # ------------------------------------------------------------------
    # Issues → triage / bug analysis
    # ------------------------------------------------------------------
    async def scan_issues(self) -> list[WorkItem]:
        slug = self.repo.slug
        items: list[WorkItem] = []
        for issue in await self.client.list_issues(slug):
            user = issue.get("user")
            if _is_bot(user) or _author_is_self(user, self.self_login):
                continue
            number = issue["number"]
            labels = [lbl["name"] for lbl in issue.get("labels", [])]
            evidence = {
                "number": number,
                "author": (user or {}).get("login", ""),
                "labels": labels,
                "comments": issue.get("comments", 0),
                "body": _excerpt(issue.get("body")),
                "age_days": round(_age_days(issue.get("created_at")), 1),
            }
            is_bug = any("bug" in label.lower() for label in labels)
            if is_bug:
                items.append(
                    WorkItem(
                        dedup_key=make_dedup_key(slug, WorkKind.FIX_BUG, f"issue#{number}"),
                        kind=WorkKind.FIX_BUG,
                        repo=slug,
                        title=f"Analyze bug: {issue.get('title', '')}",
                        description=_excerpt(issue.get("body")),
                        url=issue.get("html_url"),
                        priority=TodoPriority.HIGH,
                        role_hint="bug-hunter",
                        discovered_by="scanner:issues",
                        evidence=evidence,
                    )
                )
            elif not labels:
                items.append(
                    WorkItem(
                        dedup_key=make_dedup_key(
                            slug, WorkKind.TRIAGE_ISSUE, f"issue#{number}"
                        ),
                        kind=WorkKind.TRIAGE_ISSUE,
                        repo=slug,
                        title=f"Triage issue: {issue.get('title', '')}",
                        description=_excerpt(issue.get("body")),
                        url=issue.get("html_url"),
                        priority=TodoPriority.MEDIUM,
                        role_hint="triage",
                        discovered_by="scanner:issues",
                        evidence=evidence,
                    )
                )
        return items

    # ------------------------------------------------------------------
    # Pull requests → review
    # ------------------------------------------------------------------
    async def scan_pulls(self) -> list[WorkItem]:
        slug = self.repo.slug
        items: list[WorkItem] = []
        for pull in await self.client.list_pulls(slug):
            user = pull.get("user")
            if pull.get("draft"):
                continue
            if _author_is_self(user, self.self_login):
                continue  # never review our own PRs
            number = pull["number"]
            age = _age_days(pull.get("created_at"))
            items.append(
                WorkItem(
                    dedup_key=make_dedup_key(slug, WorkKind.REVIEW_PR, f"pr#{number}"),
                    kind=WorkKind.REVIEW_PR,
                    repo=slug,
                    title=f"Review PR: {pull.get('title', '')}",
                    description=_excerpt(pull.get("body")),
                    url=pull.get("html_url"),
                    priority=TodoPriority.HIGH if age > 3 else TodoPriority.MEDIUM,
                    role_hint="reviewer",
                    discovered_by="scanner:pulls",
                    evidence={
                        "number": number,
                        "author": (user or {}).get("login", ""),
                        "base": (pull.get("base") or {}).get("ref", ""),
                        "head": (pull.get("head") or {}).get("ref", ""),
                        "age_days": round(age, 1),
                        "is_bot_author": _is_bot(user),
                    },
                )
            )
        return items

    # ------------------------------------------------------------------
    # CI → failing workflow investigation
    # ------------------------------------------------------------------
    async def scan_ci(self) -> list[WorkItem]:
        slug = self.repo.slug
        try:
            repo_data = await self.client.get_repo(slug)
        except GitHubError:
            return []
        default_branch = repo_data.get("default_branch", "main")
        runs = await self.client.list_workflow_runs(slug, branch=default_branch)

        # Latest run per workflow; only standing failures matter.
        latest: dict[str, dict[str, Any]] = {}
        for run in runs:
            key = str(run.get("path") or run.get("name") or run.get("workflow_id"))
            if key not in latest:
                latest[key] = run

        items: list[WorkItem] = []
        for path, run in latest.items():
            if run.get("conclusion") != "failure":
                continue
            items.append(
                WorkItem(
                    dedup_key=make_dedup_key(slug, WorkKind.INVESTIGATE_CI, path),
                    kind=WorkKind.INVESTIGATE_CI,
                    repo=slug,
                    title=f"Investigate failing workflow: {run.get('name', path)}",
                    description=(
                        f"Latest run of {path} on {default_branch} concluded 'failure'."
                    ),
                    url=run.get("html_url"),
                    priority=TodoPriority.HIGH,
                    role_hint="janitor",
                    discovered_by="scanner:ci",
                    evidence={
                        "workflow_path": path,
                        "run_id": run.get("id"),
                        "run_number": run.get("run_number"),
                        "event": run.get("event"),
                        "branch": default_branch,
                        "head_sha": (run.get("head_sha") or "")[:12],
                    },
                )
            )
        return items

    # ------------------------------------------------------------------
    # Docs → missing/thin README
    # ------------------------------------------------------------------
    async def scan_docs(self) -> list[WorkItem]:
        slug = self.repo.slug
        readme = await self.client.get_readme(slug)
        if readme is not None and len(readme) >= 300:
            return []
        missing = readme is None
        return [
            WorkItem(
                dedup_key=make_dedup_key(slug, WorkKind.WRITE_DOCS, "readme"),
                kind=WorkKind.WRITE_DOCS,
                repo=slug,
                title=(
                    f"Write a README for {slug}"
                    if missing
                    else f"Expand the thin README of {slug}"
                ),
                description=(
                    "The repository has no README."
                    if missing
                    else f"The README is only {len(readme or '')} characters."
                ),
                priority=TodoPriority.MEDIUM,
                role_hint="doc-writer",
                discovered_by="scanner:docs",
                evidence={"path": "README.md", "missing": missing},
            )
        ]

    # ------------------------------------------------------------------
    # Articles → opt-in writing cadence
    # ------------------------------------------------------------------
    async def scan_article_cadence(self) -> list[WorkItem]:
        slug = self.repo.slug
        # Weekly cadence: the anchor includes the ISO week so a new item
        # appears at most once per week and dedups within the week.
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        return [
            WorkItem(
                dedup_key=make_dedup_key(slug, WorkKind.WRITE_ARTICLE, f"week:{week}"),
                kind=WorkKind.WRITE_ARTICLE,
                repo=slug,
                title=f"Write this week's article for {slug}",
                description=(
                    "Draft an article about recent activity, features, or "
                    "lessons from this repository."
                ),
                priority=TodoPriority.LOW,
                role_hint="author",
                discovered_by="scanner:articles",
                evidence={"week": week},
            )
        ]


async def discover_all(
    client: GitHubClient,
    settings: FleetSettings,
    *,
    self_login: str | None = None,
    repos: list[str] | None = None,
) -> list[WorkItem]:
    """Run discovery across the roster (optionally filtered to ``repos``)."""
    wanted = set(repos) if repos else None
    items: list[WorkItem] = []
    for repo in settings.repos:
        if wanted is not None and repo.slug not in wanted:
            continue
        discovery = RepoDiscovery(client, repo, settings, self_login=self_login)
        items.extend(await discovery.discover())
    return items


def mark_stale(items: list[WorkItem], *, stale_after_days: int) -> list[WorkItem]:
    """Return queue items whose underlying signal is older than the horizon.

    The orchestrator skips these rather than acting on ancient state.
    """
    horizon = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
    return [item for item in items if item.updated_at < horizon]
