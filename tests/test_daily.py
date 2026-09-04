"""The daily harness: docs sweep, review sweep, merge sweep (no network)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.helpers_github import FakeGitHub
from tests.test_dispatcher import FakeRouter
from wtd.config import WTDConfig
from wtd.fleet.balancer import CapacityBalancer, Lane
from wtd.fleet.daily import DailyHarness
from wtd.fleet.dispatcher import CycleBudget, Dispatcher
from wtd.fleet.mergegate import APPROVAL_SHA_KEY
from wtd.fleet.models import WorkItem, WorkKind, make_dedup_key
from wtd.fleet.roles import builtin_roles
from wtd.fleet.scheduler import Assignment
from wtd.fleet.settings import DailyConfig, FleetSettings, MergeConfig, RepoConfig
from wtd.fleet.state import FleetState

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40


def stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def commit(days_ago: float) -> dict:
    return {"sha": "c" * 40, "commit": {"committer": {"date": stamp(days_ago)}}}


def b64(text: str) -> dict:
    return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}


def pr_payload(**overrides) -> dict:
    base = {
        "number": 7,
        "title": "docs: explain the thing",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "labels": [],
        "body": "a change",
        "user": {"login": "alice", "type": "User"},
        "base": {"ref": "main"},
        "head": {"ref": "feat", "sha": HEAD},
        "html_url": "https://github.com/o/r/pull/7",
    }
    base.update(overrides)
    return base


def settings(**overrides) -> FleetSettings:
    kwargs = {
        "repos": [RepoConfig(slug="o/r")],
        "daily": DailyConfig(),
        "merge": MergeConfig(),
    }
    kwargs.update(overrides)
    return FleetSettings(**kwargs)


@pytest.fixture
def fake() -> FakeGitHub:
    fg = FakeGitHub()
    fg.route("GET", "/repos/o/r", {"default_branch": "main"})
    fg.route("GET", "/repos/o/r/readme", b64("# Project\n" + "x" * 500))
    fg.route("GET", "/repos/o/r/commits", [commit(0)])
    fg.route("GET", "/repos/o/r/pulls", [])
    return fg


def harness(fake: FakeGitHub, tmp_path: Path, cfg: FleetSettings | None = None):
    state = FleetState(tmp_path / "fleet").load()
    return DailyHarness(fake.client, cfg or settings(), state, now=NOW), state


class TestDocsSweep:
    async def test_current_docs_queue_nothing(self, fake: FakeGitHub, tmp_path: Path):
        sweep, state = harness(fake, tmp_path)
        checks = await sweep.docs_sweep()
        assert [c.repo for c in checks] == ["o/r"]
        assert not checks[0].assessment.needs_update
        assert state.items == {}

    async def test_stale_docs_queue_one_item_per_day(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        # The newest commit is today; the newest docs commit is 60 days old,
        # with a wall of commits since.
        fake.route("GET", "/repos/o/r/commits", [commit(0)] * 30)
        sweep, state = harness(fake, tmp_path)

        # The path-filtered read has to answer differently from the general
        # one, so drive it through a per-request router.
        async def _commits(repo, *, path=None, since=None, sha=None, per_page=30):
            if path:
                return [commit(60)]
            if since:
                return [commit(i) for i in range(30)]
            return [commit(0)]

        sweep.github.list_commits = _commits  # type: ignore[method-assign]

        checks = await sweep.docs_sweep()
        assert checks[0].assessment.needs_update
        assert checks[0].queued
        item = next(iter(state.items.values()))
        assert item.kind is WorkKind.WRITE_DOCS
        assert item.role_hint == "doc-writer"
        assert item.evidence["day"] == "2026-09-03"

        # Same day, second run: refreshed, never duplicated.
        again = await sweep.docs_sweep()
        assert not again[0].queued
        assert len(state.items) == 1

    async def test_missing_readme_still_fires(self, fake: FakeGitHub, tmp_path: Path):
        fake.route("GET", "/repos/o/r/readme", (404, {"message": "no"}))
        sweep, state = harness(fake, tmp_path)
        checks = await sweep.docs_sweep()
        assert checks[0].queued
        assert "no README" in checks[0].assessment.summary

    async def test_a_broken_repo_does_not_sink_the_sweep(self, tmp_path: Path):
        fg = FakeGitHub()
        fg.route("GET", "/repos/o/good/readme", b64("# ok\n" + "x" * 500))
        fg.route("GET", "/repos/o/good/commits", [commit(0)])
        cfg = settings(repos=[RepoConfig(slug="o/gone"), RepoConfig(slug="o/good")])
        sweep, _ = harness(fg, tmp_path, cfg)
        checks = await sweep.docs_sweep()
        assert [c.repo for c in checks] == ["o/gone", "o/good"]
        assert checks[0].error and not checks[0].queued
        assert checks[1].error is None


class TestReviewSweep:
    async def test_every_open_pr_is_queued_keyed_by_head(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        fake.route("GET", "/repos/o/r/pulls", [pr_payload()])
        sweep, state = harness(fake, tmp_path)
        targets = await sweep.review_sweep()
        assert targets[0].queued
        item = next(iter(state.items.values()))
        assert item.kind is WorkKind.REVIEW_PR
        assert item.role_hint == "reviewer"
        assert item.evidence["head_sha"] == HEAD

        # Unchanged head: no second review.
        assert not (await sweep.review_sweep())[0].queued
        assert len(state.items) == 1

    async def test_a_push_earns_a_fresh_review(self, fake: FakeGitHub, tmp_path: Path):
        fake.route("GET", "/repos/o/r/pulls", [pr_payload()])
        sweep, state = harness(fake, tmp_path)
        await sweep.review_sweep()
        fake.route(
            "GET", "/repos/o/r/pulls", [pr_payload(head={"ref": "feat", "sha": "b" * 40})]
        )
        assert (await sweep.review_sweep())[0].queued
        assert len(state.items) == 2

    async def test_drafts_are_reviewed_but_can_be_switched_off(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        fake.route("GET", "/repos/o/r/pulls", [pr_payload(draft=True)])
        sweep, _ = harness(fake, tmp_path)
        assert (await sweep.review_sweep())[0].queued

        cfg = settings(daily=DailyConfig(review_drafts=False))
        sweep2, state2 = harness(fake, tmp_path / "b", cfg)
        target = (await sweep2.review_sweep())[0]
        assert not target.queued
        assert "draft" in target.reason
        assert state2.items == {}

    async def test_fleet_authored_prs_are_flagged_in_evidence(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        body = "docs\n\n<!-- wtd-fleet:o/r:write_docs:abc -->"
        fake.route("GET", "/repos/o/r/pulls", [pr_payload(body=body)])
        sweep, state = harness(fake, tmp_path)
        await sweep.review_sweep()
        assert next(iter(state.items.values())).evidence["fleet_authored"] is True


def route_merge_inputs(fake: FakeGitHub, *, pull: dict, checks: list, reviews=()) -> None:
    fake.route("GET", "/repos/o/r/pulls/7", pull)
    fake.route("GET", f"/repos/o/r/commits/{pull['head']['sha']}/check-runs",
               {"check_runs": checks})
    fake.route("GET", f"/repos/o/r/commits/{pull['head']['sha']}/status",
               {"state": "success", "statuses": []})
    fake.route("GET", "/repos/o/r/pulls/7/reviews", list(reviews))


GREEN_CHECKS = [{"name": "CI", "status": "completed", "conclusion": "success"}]


def approved_item(sha: str = HEAD) -> WorkItem:
    return WorkItem(
        dedup_key=make_dedup_key("o/r", WorkKind.REVIEW_PR, f"pr#7@{sha[:12]}"),
        kind=WorkKind.REVIEW_PR,
        repo="o/r",
        title="Review PR #7",
        evidence={"number": 7, "head_sha": sha, APPROVAL_SHA_KEY: sha},
    )


class TestMergeSweep:
    def merge_settings(self, **merge_kwargs) -> FleetSettings:
        merge = MergeConfig(enabled=True, **merge_kwargs)
        return settings(repos=[RepoConfig(slug="o/r", merge=True)], merge=merge)

    async def test_approved_and_green_merges_in_apply_mode(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        fake.route("PUT", "/repos/o/r/pulls/7/merge", {"merged": True})
        sweep, state = harness(fake, tmp_path, self.merge_settings())
        state.enqueue(approved_item())

        attempts = await sweep.merge_sweep(apply=True)
        assert attempts[0].merged
        method, path, body = fake.writes()[0]
        assert (method, path) == ("PUT", "/repos/o/r/pulls/7/merge")
        # The merge is conditional on the exact reviewed commit.
        assert body["sha"] == HEAD
        assert body["merge_method"] == "squash"
        # The approval is retired so a reopened PR cannot ride it.
        assert APPROVAL_SHA_KEY not in next(iter(state.items.values())).evidence

    async def test_dry_run_evaluates_but_never_merges(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        sweep, state = harness(fake, tmp_path, self.merge_settings())
        state.enqueue(approved_item())

        attempts = await sweep.merge_sweep(apply=False)
        assert attempts[0].decision.allowed
        assert not attempts[0].merged
        assert fake.writes() == []

    async def test_red_ci_holds_the_merge(self, fake: FakeGitHub, tmp_path: Path):
        route_merge_inputs(
            fake,
            pull=pr_payload(),
            checks=[{"name": "CI", "status": "completed", "conclusion": "failure"}],
        )
        sweep, state = harness(fake, tmp_path, self.merge_settings())
        state.enqueue(approved_item())

        attempts = await sweep.merge_sweep(apply=True)
        assert not attempts[0].merged
        assert "CI is not green" in attempts[0].decision.reason
        assert fake.writes() == []

    async def test_unapproved_prs_are_never_considered(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        sweep, state = harness(fake, tmp_path, self.merge_settings())
        item = approved_item()
        item.evidence.pop(APPROVAL_SHA_KEY)
        state.enqueue(item)
        assert await sweep.merge_sweep(apply=True) == []

    async def test_repo_without_opt_in_is_skipped(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        cfg = settings(
            repos=[RepoConfig(slug="o/r", merge=False)], merge=MergeConfig(enabled=True)
        )
        sweep, state = harness(fake, tmp_path, cfg)
        state.enqueue(approved_item())
        assert await sweep.merge_sweep(apply=True) == []
        assert fake.writes() == []

    async def test_max_per_cycle_caps_the_sweep(self, fake: FakeGitHub, tmp_path: Path):
        for number in (7, 8):
            pull = pr_payload(number=number)
            fake.route("GET", f"/repos/o/r/pulls/{number}", pull)
            fake.route("GET", f"/repos/o/r/pulls/{number}/reviews", [])
            fake.route("PUT", f"/repos/o/r/pulls/{number}/merge", {"merged": True})
        fake.route("GET", f"/repos/o/r/commits/{HEAD}/check-runs",
                   {"check_runs": GREEN_CHECKS})
        fake.route("GET", f"/repos/o/r/commits/{HEAD}/status",
                   {"state": "success", "statuses": []})
        sweep, state = harness(fake, tmp_path, self.merge_settings(max_per_cycle=1))
        state.enqueue(approved_item())
        second = approved_item()
        second.dedup_key = make_dedup_key("o/r", WorkKind.REVIEW_PR, "pr#8@x")
        second.evidence["number"] = 8
        state.enqueue(second)

        attempts = await sweep.merge_sweep(apply=True)
        assert sum(1 for a in attempts if a.merged) == 1

    async def test_inspect_merges_never_writes(self, fake: FakeGitHub, tmp_path: Path):
        fake.route("GET", "/repos/o/r/pulls", [pr_payload()])
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        sweep, _ = harness(fake, tmp_path, self.merge_settings())

        strict = await sweep.inspect_merges(require_approval=True)
        assert not strict[0].decision.allowed  # no approval on record
        relaxed = await sweep.inspect_merges(require_approval=False)
        assert relaxed[0].decision.allowed
        assert fake.writes() == []


def review_reply(merge: bool) -> str:
    actions = [{"type": "comment", "body": "Looks correct; CI is green."}]
    if merge:
        actions.append({"type": "merge_pr", "body": "Docs-only, green, low risk."})
    return json.dumps({"summary": "Reviewed", "actions": actions, "discovered": []})


class TestDispatcherMergePath:
    def world(self, fake: FakeGitHub, tmp_path: Path, cfg: FleetSettings):
        state = FleetState(tmp_path / "fleet").load()
        balancer = CapacityBalancer([Lane("claude-code", 1_000_000)])
        router = FakeRouter(review_reply(merge=True))
        dispatcher = Dispatcher(
            WTDConfig(), cfg, state, balancer, fake.client, router
        )
        item = WorkItem(
            dedup_key=make_dedup_key("o/r", WorkKind.REVIEW_PR, "pr#7@aaaa"),
            kind=WorkKind.REVIEW_PR,
            repo="o/r",
            title="Review PR #7",
            url="https://github.com/o/r/pull/7",
            evidence={"number": 7, "head_sha": HEAD, "author": "alice"},
        )
        state.enqueue(item)
        return dispatcher, state, Assignment(item=item, role=builtin_roles()["reviewer"])

    def merge_on(self) -> FleetSettings:
        return settings(
            repos=[RepoConfig(slug="o/r", merge=True)],
            merge=MergeConfig(enabled=True),
        )

    async def test_reviewer_merge_recommendation_merges_when_green(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        fake.route("GET", "/repos/o/r/issues/7/comments", [])
        fake.route("POST", "/repos/o/r/issues/7/comments", {"html_url": "u"})
        fake.route("PUT", "/repos/o/r/pulls/7/merge", {"merged": True})
        dispatcher, state, assignment = self.world(fake, tmp_path, self.merge_on())

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(5))
        merge_action = [a for a in run.actions if a.type.value == "merge_pr"][0]
        assert merge_action.applied
        assert ("PUT", "/repos/o/r/pulls/7/merge") in [(m, p) for m, p, _ in fake.writes()]

    async def test_gate_refusal_records_the_approval_for_later(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        # CI still running: the reviewer approved, the gate says "not yet".
        route_merge_inputs(
            fake, pull=pr_payload(), checks=[{"name": "CI", "status": "in_progress"}]
        )
        fake.route("GET", "/repos/o/r/issues/7/comments", [])
        fake.route("POST", "/repos/o/r/issues/7/comments", {"html_url": "u"})
        dispatcher, state, assignment = self.world(fake, tmp_path, self.merge_on())

        budget = CycleBudget(5)
        run = await dispatcher.run(assignment, apply=True, write_budget=budget)
        merge_action = [a for a in run.actions if a.type.value == "merge_pr"][0]
        assert not merge_action.applied
        assert "merge gate refused" in (merge_action.error or "")
        # The approval survives for tomorrow's sweep, pinned to the commit.
        assert assignment.item.evidence[APPROVAL_SHA_KEY] == HEAD
        # A refusal writes nothing, so it must not spend the write budget:
        # one comment posted, one slot refunded.
        assert budget.remaining == 4

    async def test_merge_is_impossible_without_the_policy(
        self, fake: FakeGitHub, tmp_path: Path
    ):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        fake.route("GET", "/repos/o/r/issues/7/comments", [])
        fake.route("POST", "/repos/o/r/issues/7/comments", {"html_url": "u"})
        dispatcher, state, assignment = self.world(fake, tmp_path, settings())

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(5))
        merge_action = [a for a in run.actions if a.type.value == "merge_pr"][0]
        assert not merge_action.applied
        assert "not enabled" in (merge_action.error or "")
        assert all(p != "/repos/o/r/pulls/7/merge" for _, p, _ in fake.writes())

    async def test_dry_run_never_merges(self, fake: FakeGitHub, tmp_path: Path):
        route_merge_inputs(fake, pull=pr_payload(), checks=GREEN_CHECKS)
        dispatcher, state, assignment = self.world(fake, tmp_path, self.merge_on())
        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert [a.applied for a in run.actions] == [False, False]
        assert fake.writes() == []
