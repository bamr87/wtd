"""The fleet orchestrator: the autonomous mechanism.

One **cycle**:

1. Honour the kill switch (``WTD_FLEET_ENABLED``).
2. Discover work across the roster (deterministic scanners) and merge it
   into the queue with dedup.
3. Plan the cycle: match pending items to agent roles with priority +
   per-repo fairness (``scheduler``).
4. Dispatch assignments concurrently under the token balancer and the
   cycle write budget (``dispatcher``).
5. Persist state and report.

``loop()`` repeats cycles forever with a sleep interval — that is the
standalone daemon. A single ``cycle()`` is also directly invokable for
cron/GitHub Actions harnesses.

``daily()`` is the once-a-day pass on top of that mechanism: the docs-drift
sweep across the roster, a review of every open pull request by the Opus 5
reviewer, and the merge gate for the ones that came back approved and green
(:mod:`wtd.fleet.daily`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from wtd.config import WTDConfig, get_config
from wtd.fleet.balancer import CapacityBalancer, LaneSnapshot, default_lanes
from wtd.fleet.daily import DailyHarness, DailyReport, gather_daily
from wtd.fleet.discovery import discover_all
from wtd.fleet.dispatcher import CycleBudget, Dispatcher
from wtd.fleet.docsdrift import utc_day
from wtd.fleet.github import GitHubClient, GitHubError
from wtd.fleet.models import AgentRunRecord, WorkStatus
from wtd.fleet.roles import load_roles
from wtd.fleet.scheduler import CyclePlan, plan_cycle, unknown_role_names
from wtd.fleet.settings import FleetSettings, load_settings
from wtd.fleet.state import FleetState
from wtd.providers import ProviderRouter

logger = logging.getLogger(__name__)


@dataclass
class CycleReport:
    enabled: bool = True
    apply: bool = False
    discovered_new: int = 0
    queue_pending: int = 0
    scheduled: int = 0
    unroutable: int = 0
    overflow: int = 0
    runs: list[AgentRunRecord] = field(default_factory=list)
    lanes: list[LaneSnapshot] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return sum(1 for r in self.runs if r.outcome.value == "completed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.runs if r.outcome.value == "failed")

    @property
    def actions_applied(self) -> int:
        return sum(1 for r in self.runs for a in r.actions if a.applied)


class FleetOrchestrator:
    """Wires config, settings, state, balancer, GitHub, and providers."""

    def __init__(
        self,
        config: WTDConfig | None = None,
        settings: FleetSettings | None = None,
        *,
        state: FleetState | None = None,
        github: GitHubClient | None = None,
        router: ProviderRouter | None = None,
        balancer: CapacityBalancer | None = None,
    ):
        self.config = config or get_config()
        self.settings = settings or load_settings(self.config)
        state_dir = self.config.fleet_state_path
        self.state = state or FleetState(state_dir).load()
        self.github = github or GitHubClient(
            self.config.github_token, api_url=self.config.github_api_url
        )
        self.router = router or ProviderRouter(self.config)
        self.balancer = balancer or CapacityBalancer(
            default_lanes(self.settings), state_dir / "capacity.json"
        )
        self.roles = load_roles(self.config, enabled=self.settings.roles_enabled or None)
        self.dispatcher = Dispatcher(
            self.config,
            self.settings,
            self.state,
            self.balancer,
            self.github,
            self.router,
        )

    # ------------------------------------------------------------------
    async def discover(self, repos: list[str] | None = None) -> int:
        """Run discovery and merge results into the queue. Returns new count."""
        try:
            self_login = await self.github.viewer_login()
        except GitHubError:
            self_login = None
        items = await discover_all(
            self.github, self.settings, self_login=self_login, repos=repos
        )
        new = sum(1 for item in items if self.state.enqueue(item))
        self.state.save()
        return new

    # ------------------------------------------------------------------
    def plan(
        self,
        *,
        repos: list[str] | None = None,
        roles: list[str] | None = None,
        max_runs: int | None = None,
    ) -> CyclePlan:
        pending = self.state.pending(max_attempts=self.settings.max_attempts)
        repo_roles = self.settings.repo_role_allowlist()
        for repo, names in unknown_role_names(self.roles, repo_roles).items():
            # Otherwise this is silent: the repo just stops being worked.
            logger.warning(
                "wtd.yml lists role(s) %s for %s that match no loaded role; "
                "work for that repo will be unroutable",
                ", ".join(names),
                repo,
            )
        return plan_cycle(
            pending,
            self.roles,
            max_runs=max_runs or self.settings.max_runs_per_cycle,
            repos=repos,
            role_names=roles,
            repo_roles=repo_roles,
        )

    # ------------------------------------------------------------------
    async def cycle(
        self,
        *,
        apply: bool | None = None,
        repos: list[str] | None = None,
        roles: list[str] | None = None,
        max_runs: int | None = None,
        skip_discovery: bool = False,
    ) -> CycleReport:
        report = CycleReport()

        if not self.config.fleet_enabled:
            report.enabled = False
            report.notes.append("fleet disabled via WTD_FLEET_ENABLED — nothing run")
            return report

        apply = self.config.fleet_apply if apply is None else apply
        report.apply = apply
        if apply and not self.config.github_token:
            apply = False
            report.apply = False
            report.notes.append(
                "apply requested but no GitHub token configured — dry-run instead"
            )

        if not self.settings.repos:
            report.notes.append(
                "no repos on the roster (set fleet.repos in wtd.yml or WTD_FLEET_REPOS)"
            )

        if not skip_discovery and self.settings.repos:
            report.discovered_new = await self.discover(repos)

        plan = self.plan(repos=repos, roles=roles, max_runs=max_runs)
        report.scheduled = len(plan.assignments)
        report.unroutable = len(plan.unroutable)
        report.overflow = len(plan.overflow)
        for item in plan.assignments:
            self.state.mark(item.item, WorkStatus.SCHEDULED)

        write_budget = CycleBudget(self.settings.max_writes_per_cycle)
        semaphore = asyncio.Semaphore(self.config.fleet_concurrency)

        async def _run(assignment) -> AgentRunRecord:
            async with semaphore:
                return await self.dispatcher.run(
                    assignment, apply=apply, write_budget=write_budget
                )

        if plan.assignments:
            report.runs = list(
                await asyncio.gather(*(_run(a) for a in plan.assignments))
            )

        self.state.prune_done()
        self.state.save()
        self.balancer.save()
        report.queue_pending = len(
            self.state.pending(max_attempts=self.settings.max_attempts)
        )
        report.lanes = self.balancer.snapshot()
        return report

    # ------------------------------------------------------------------
    def harness(self, *, now=None) -> DailyHarness:
        return DailyHarness(
            self.github,
            self.settings,
            self.state,
            bot_marker=self.config.bot_marker,
            now=now,
        )

    async def daily(
        self,
        *,
        apply: bool | None = None,
        repos: list[str] | None = None,
        max_runs: int | None = None,
        run_agents: bool = True,
        discover: bool = False,
    ) -> tuple[DailyReport, CycleReport | None]:
        """The daily pass: docs sweep → review sweep → agents → merge gate.

        Ordering matters. The sweeps queue work first so the cycle that
        follows can act on it in the same run, and the merge sweep goes
        last so a review that lands an approval this morning can merge the
        same morning — while yesterday's approvals, whose CI has since gone
        green, merge without another model call.
        """
        report = DailyReport(day=utc_day(), apply=False)

        if not self.config.fleet_enabled:
            report.notes.append("fleet disabled via WTD_FLEET_ENABLED — nothing run")
            return report, None

        apply = self.config.fleet_apply if apply is None else apply
        if apply and not self.config.github_token:
            apply = False
            report.notes.append(
                "apply requested but no GitHub token configured — dry-run instead"
            )
        report.apply = apply

        if not self.settings.repos:
            report.notes.append(
                "no repos on the roster (set fleet.repos in wtd.yml or WTD_FLEET_REPOS)"
            )
            return report, None

        harness = self.harness()
        report.docs, report.reviews = await gather_daily(
            harness,
            repos=repos,
            docs=self.settings.daily.docs,
            review=self.settings.daily.review,
        )
        self.state.save()

        cycle_report: CycleReport | None = None
        if run_agents:
            cycle_report = await self.cycle(
                apply=apply,
                repos=repos,
                max_runs=max_runs,
                skip_discovery=not discover,
            )

        report.merges = await harness.merge_sweep(repos, apply=apply)
        if not any(
            self.settings.merge_policy_for(slug).enabled
            for slug in (repos or self.settings.repo_slugs())
        ):
            report.notes.append(
                "merging is off for every repo in scope "
                "(fleet.merge.enabled + per-repo 'merge: true' both required)"
            )
        self.state.save()
        return report, cycle_report

    # ------------------------------------------------------------------
    async def loop(
        self,
        *,
        apply: bool | None = None,
        interval_seconds: int | None = None,
        max_cycles: int | None = None,
        on_report=None,
    ) -> None:
        """Run cycles forever (or ``max_cycles`` times) — the daemon."""
        interval = (
            self.config.fleet_interval_seconds
            if interval_seconds is None
            else interval_seconds
        )
        cycles = 0
        while True:
            try:
                report = await self.cycle(apply=apply)
            except Exception:
                logger.exception("fleet cycle crashed; continuing after interval")
                report = None
            if on_report is not None and report is not None:
                on_report(report)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            await asyncio.sleep(interval)

    async def aclose(self) -> None:
        await self.github.aclose()
