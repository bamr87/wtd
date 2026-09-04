"""The dispatcher: run one agent against one work item, end to end.

Pipeline per assignment:

1. Build bounded context for the item (``context.py``).
2. Reserve lane capacity from the balancer; defer when budgets are spent.
3. Generate through the provider router (Claude Code → Anthropic API),
   preferring the reserved lane.
4. Parse + validate the structured outcome (``outcome.py``).
5. Apply actions to GitHub — only in apply mode, only within the cycle's
   write budget, always with dedup markers and loop guards.
6. Enqueue agent-discovered work items (the flywheel).
7. Record everything in the run ledger.

Dry-run is the default posture: without ``--apply`` (or
``WTD_FLEET_APPLY=true``) agents think and their planned actions are
recorded, but nothing is written to GitHub.
"""

from __future__ import annotations

import asyncio
import logging

from wtd.config import WTDConfig
from wtd.fleet.balancer import CapacityBalancer
from wtd.fleet.context import ContextBuilder
from wtd.fleet.github import GitHubClient, GitHubError, has_marker, marker_comment
from wtd.fleet.mergegate import (
    APPROVAL_REASON_KEY,
    APPROVAL_SHA_KEY,
    evaluate_merge,
    summarize_ci,
)
from wtd.fleet.models import (
    ActionType,
    AgentRunRecord,
    ProposedAction,
    RunOutcome,
    WorkItem,
    WorkStatus,
    slugify,
    utcnow,
)
from wtd.fleet.outcome import OutcomeError, output_contract, parse_outcome
from wtd.fleet.roles import AgentRole
from wtd.fleet.scheduler import Assignment
from wtd.fleet.settings import FleetSettings
from wtd.fleet.state import FleetState
from wtd.providers import ProviderError, ProviderRouter

logger = logging.getLogger(__name__)

_RATE_LIMIT_COOLDOWN_SECONDS = 900

_KIND_INSTRUCTIONS = {
    "triage_issue": "Triage this issue per your role. Comment and label it.",
    "fix_bug": "Analyze this bug per your role and comment your findings.",
    "review_pr": (
        "Review this pull request per your role and comment your review. If "
        "you would merge it yourself, also request the merge — the platform "
        "re-verifies CI and policy before acting on that."
    ),
    "investigate_ci": "Diagnose this standing CI failure per your role.",
    "write_docs": "Write the documentation this task describes, as a draft PR.",
    "improve_code": "Implement the smallest safe improvement for this TODO.",
    "write_article": "Write the article this task describes, as a draft PR.",
    "custom": "Complete this task per your role.",
}


class CycleBudget:
    """Shared, race-safe write budget for one orchestrator cycle."""

    def __init__(self, max_writes: int):
        self.remaining = max_writes
        self._lock = asyncio.Lock()

    async def take(self) -> bool:
        async with self._lock:
            if self.remaining <= 0:
                return False
            self.remaining -= 1
            return True

    async def refund(self) -> None:
        async with self._lock:
            self.remaining += 1


class Dispatcher:
    def __init__(
        self,
        config: WTDConfig,
        settings: FleetSettings,
        state: FleetState,
        balancer: CapacityBalancer,
        github: GitHubClient,
        router: ProviderRouter,
    ):
        self.config = config
        self.settings = settings
        self.state = state
        self.balancer = balancer
        self.github = github
        self.router = router
        self.context_builder = ContextBuilder(github, bot_marker=config.bot_marker)

    # ------------------------------------------------------------------
    def build_prompt(self, assignment: Assignment, context: str) -> str:
        item, role = assignment.item, assignment.role
        instruction = _KIND_INSTRUCTIONS.get(item.kind.value, _KIND_INSTRUCTIONS["custom"])
        return (
            f"# Fleet work item\n"
            f"Kind: {item.kind.value}\n"
            f"Repository: {item.repo}\n"
            f"Title: {item.title}\n"
            f"Priority: {item.priority.value}\n\n"
            f"# Your task\n{instruction}\n\n"
            f"# Evidence\n{context}\n\n"
            f"{output_contract(role)}"
        )

    # ------------------------------------------------------------------
    async def run(
        self, assignment: Assignment, *, apply: bool, write_budget: CycleBudget
    ) -> AgentRunRecord:
        item, role = assignment.item, assignment.role
        run = AgentRunRecord(
            item_id=item.id,
            dedup_key=item.dedup_key,
            kind=item.kind,
            repo=item.repo,
            role=role.name,
            dry_run=not apply,
        )
        self.state.mark(item, WorkStatus.RUNNING)
        item.attempts += 1

        # 1. Context
        try:
            context = await self.context_builder.build(item)
        except GitHubError as exc:
            return self._fail(run, item, f"context fetch failed: {exc}")

        # 2. Capacity
        lane = self.balancer.pick(role.est_tokens)
        if lane is None:
            run.outcome = RunOutcome.SKIPPED
            run.summary = "deferred: no lane has token budget headroom"
            run.finished_at = utcnow()
            self.state.mark(item, WorkStatus.DEFERRED)
            item.attempts -= 1  # deferral is not a failed attempt
            self.state.record_run(run)
            return run
        run.lane = lane

        # 3. Generate
        prompt = self.build_prompt(assignment, context)
        try:
            result = await self.router.generate(
                prompt,
                system=role.full_system_prompt(),
                model=role.model,
                max_tokens=role.max_tokens,
                preferred=lane,
            )
        except ProviderError as exc:
            self.balancer.release(lane, role.est_tokens)
            if "rate limited" in str(exc).lower():
                self.balancer.cooldown(lane, _RATE_LIMIT_COOLDOWN_SECONDS)
            return self._fail(run, item, str(exc))

        run.provider = result.provider
        run.model = result.model
        run.lane = result.provider  # the lane that actually served
        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        run.cost_usd = result.cost_usd
        run.duration_ms = result.duration_ms
        if result.provider != lane:
            # Served by a failover lane: release the original reservation and
            # bill the serving lane.
            self.balancer.release(lane, role.est_tokens)
            self.balancer.record(
                result.provider,
                est_tokens=0,
                tokens=result.total_tokens,
                usd=result.cost_usd,
            )
        else:
            self.balancer.record(
                lane,
                est_tokens=role.est_tokens,
                tokens=result.total_tokens,
                usd=result.cost_usd,
            )

        # 4. Parse outcome
        try:
            outcome = parse_outcome(
                result.text,
                role,
                item,
                max_discovered=self.settings.max_discovered_per_run,
            )
        except OutcomeError as exc:
            return self._fail(run, item, f"unusable agent reply: {exc}")

        run.summary = outcome.summary
        run.actions = outcome.actions
        if outcome.rejected:
            logger.info(
                "run %s: rejected %d agent requests: %s",
                run.id,
                len(outcome.rejected),
                "; ".join(outcome.rejected[:5]),
            )

        # 5. Apply
        if apply and role.writes:
            for action in outcome.actions:
                await self._apply_action(action, item, role, write_budget)
        # 6. Flywheel: enqueue discovered work
        for discovered in outcome.discovered:
            if self.state.enqueue(discovered):
                run.discovered += 1

        run.outcome = RunOutcome.COMPLETED
        run.finished_at = utcnow()
        self.state.mark(item, WorkStatus.DONE)
        self.state.record_run(run)
        return run

    # ------------------------------------------------------------------
    def _fail(self, run: AgentRunRecord, item: WorkItem, error: str) -> AgentRunRecord:
        run.outcome = RunOutcome.FAILED
        run.error = error[:2000]
        run.finished_at = utcnow()
        exhausted = item.attempts >= self.settings.max_attempts
        self.state.mark(
            item,
            WorkStatus.FAILED if exhausted else WorkStatus.QUEUED,
            error=error[:500],
        )
        self.state.record_run(run)
        return run

    # ------------------------------------------------------------------
    def _signature(self, role_name: str, item: WorkItem) -> str:
        return (
            f"\n\n---\n🤖 wtd fleet · `{role_name}` agent · "
            f"item `{item.dedup_key}`\n"
            f"{marker_comment(self.config.bot_marker, item.dedup_key)}"
        )

    async def _already_answered(self, item: WorkItem) -> bool:
        """True when a fleet comment for this item already exists."""
        number = item.evidence.get("number")
        if not number:
            return False
        try:
            comments = await self.github.list_issue_comments(item.repo, int(number))
        except GitHubError:
            return False
        return any(
            has_marker(str(c.get("body", "")), self.config.bot_marker, item.dedup_key)
            for c in comments
        )

    async def _issue_already_filed(self, item: WorkItem) -> bool:
        """True when the fleet already filed an issue for this dedup key."""
        try:
            issues = await self.github.list_issues(item.repo, per_page=50)
        except GitHubError:
            return False
        return any(
            has_marker(str(i.get("body", "")), self.config.bot_marker, item.dedup_key)
            for i in issues
        )

    async def _apply_action(
        self,
        action: ProposedAction,
        item: WorkItem,
        role: AgentRole,
        write_budget: CycleBudget,
    ) -> None:
        role_name = role.name
        if not await write_budget.take():
            action.error = "cycle write budget exhausted"
            return
        try:
            if action.type == ActionType.COMMENT:
                number = item.evidence.get("number")
                if not number:
                    action.error = "no issue/PR number to comment on"
                    await write_budget.refund()
                    return
                if await self._already_answered(item):
                    action.error = "skipped: fleet already commented on this item"
                    await write_budget.refund()
                    return
                created = await self.github.create_issue_comment(
                    item.repo, int(number), action.body + self._signature(role_name, item)
                )
                action.result_url = created.get("html_url")

            elif action.type == ActionType.ADD_LABELS:
                number = item.evidence.get("number")
                if not number:
                    action.error = "no issue/PR number to label"
                    await write_budget.refund()
                    return
                await self.github.add_labels(item.repo, int(number), action.labels)
                action.result_url = item.url

            elif action.type == ActionType.CREATE_ISSUE:
                if await self._issue_already_filed(item):
                    action.error = "skipped: fleet already filed an issue for this item"
                    await write_budget.refund()
                    return
                created = await self.github.create_issue(
                    item.repo,
                    action.title,
                    action.body + self._signature(role_name, item),
                )
                action.result_url = created.get("html_url")

            elif action.type == ActionType.MERGE_PR:
                await self._apply_merge(action, item, write_budget)

            elif action.type == ActionType.PROPOSE_PR:
                repo_data = await self.github.get_repo(item.repo)
                base = repo_data.get("default_branch", "main")
                branch = f"{action.branch}-{slugify(item.dedup_key.split(':')[-1], 12)}"
                created = await self.github.propose_pr(
                    item.repo,
                    branch=branch,
                    base=base,
                    files={f["path"]: f["content"] for f in action.files},
                    title=action.title,
                    body=action.body + self._signature(role_name, item),
                )
                action.result_url = created.get("html_url")

            action.applied = action.error is None
        except GitHubError as exc:
            action.error = str(exc)[:500]
            await write_budget.refund()

    # ------------------------------------------------------------------
    async def _apply_merge(
        self, action: ProposedAction, item: WorkItem, write_budget: CycleBudget
    ) -> None:
        """Act on a reviewer's merge recommendation — through the gate.

        The agent's verdict is one of two keys. This is the other: live
        GitHub state, re-read now, judged by pure code. When the gate
        refuses, the approval is still recorded against the head commit the
        reviewer read, so the daily merge sweep can act the moment the
        remaining condition (usually "CI is still running") clears.
        """
        number = item.evidence.get("number")
        if not number:
            action.error = "no pull request number to merge"
            await write_budget.refund()
            return
        head_sha = str(item.evidence.get("head_sha") or "")
        policy = self.settings.merge_policy_for(item.repo)

        inputs = await self.github.merge_inputs(item.repo, int(number))
        pull = inputs["pull"]
        if not head_sha:
            head_sha = str(inputs["head_sha"])

        # The approval is pinned to the commit the reviewer actually read.
        item.evidence[APPROVAL_SHA_KEY] = head_sha
        item.evidence[APPROVAL_REASON_KEY] = action.body[:500]

        decision = evaluate_merge(
            pull,
            policy=policy,
            ci=summarize_ci(inputs["check_runs"], inputs["combined_status"]),
            reviews=inputs["reviews"],
            approved_sha=head_sha,
            fleet_authored=has_marker(str(pull.get("body") or ""), self.config.bot_marker),
            repo=item.repo,
        )
        if not decision.allowed:
            # A refusal writes nothing, so it must not spend the cycle's
            # write budget — the approval simply waits for the next sweep.
            action.error = f"merge gate refused: {decision.reason}"
            await write_budget.refund()
            return

        await self.github.merge_pull(
            item.repo,
            int(number),
            sha=decision.head_sha,
            method=decision.method,
            commit_title=f"{decision.title} (#{number})",
        )
        item.evidence.pop(APPROVAL_SHA_KEY, None)
        action.result_url = item.url or decision.url
