"""End-to-end dispatcher tests with fake provider + fake GitHub."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers_github import FakeGitHub
from wtd.config import WTDConfig
from wtd.fleet.balancer import CapacityBalancer, Lane
from wtd.fleet.dispatcher import CycleBudget, Dispatcher
from wtd.fleet.models import (
    RunOutcome,
    WorkItem,
    WorkKind,
    WorkStatus,
    make_dedup_key,
)
from wtd.fleet.roles import builtin_roles
from wtd.fleet.scheduler import Assignment
from wtd.fleet.settings import FleetSettings
from wtd.fleet.state import FleetState
from wtd.providers import GenerationResult, ProviderError


class FakeRouter:
    def __init__(self, text: str, *, provider: str = "claude-code", fail: Exception | None = None):
        self.text = text
        self.provider = provider
        self.fail = fail
        self.calls: list[dict] = []

    async def generate(self, prompt, system="", *, model=None, max_tokens=None, preferred=None):
        self.calls.append(
            {"prompt": prompt, "system": system, "model": model, "preferred": preferred}
        )
        if self.fail is not None:
            raise self.fail
        return GenerationResult(
            text=self.text,
            provider=self.provider,
            model=model or "claude-opus-5",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.02,
            duration_ms=1200,
        )


def triage_reply(body: str = "Thanks — labeled.") -> str:
    return json.dumps(
        {
            "summary": "Triaged the issue",
            "actions": [
                {"type": "comment", "body": body},
                {"type": "add_labels", "labels": ["bug"]},
            ],
            "discovered": [
                {"kind": "write_docs", "title": "Document setup steps", "priority": "low"}
            ],
        }
    )


def make_world(tmp_path: Path, *, router: FakeRouter, lanes=None):
    config = WTDConfig()
    settings = FleetSettings(max_writes_per_cycle=5)
    state = FleetState(tmp_path / "fleet").load()
    balancer = CapacityBalancer(
        lanes or [Lane("claude-code", 1_000_000), Lane("anthropic", 1_000_000)]
    )
    fake = FakeGitHub()
    fake.route("GET", "/repos/o/r/issues/5/comments", [])
    fake.route("POST", "/repos/o/r/issues/5/comments",
               {"html_url": "https://github.com/o/r/issues/5#c1"})
    fake.route("POST", "/repos/o/r/issues/5/labels", [])
    dispatcher = Dispatcher(config, settings, state, balancer, fake.client, router)
    return dispatcher, state, balancer, fake


def triage_assignment() -> Assignment:
    item = WorkItem(
        dedup_key=make_dedup_key("o/r", WorkKind.TRIAGE_ISSUE, "issue#5"),
        kind=WorkKind.TRIAGE_ISSUE,
        repo="o/r",
        title="Triage issue: example",
        evidence={"number": 5, "author": "alice", "labels": [], "body": "help"},
    )
    return Assignment(item=item, role=builtin_roles()["triage"])


class TestDryRun:
    async def test_dry_run_records_but_never_writes(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, balancer, fake = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(
            assignment, apply=False, write_budget=CycleBudget(5)
        )

        assert run.outcome == RunOutcome.COMPLETED
        assert run.dry_run is True
        assert len(run.actions) == 2
        assert all(not a.applied for a in run.actions)
        assert fake.writes() == []  # nothing posted
        assert assignment.item.status == WorkStatus.DONE
        assert state.recent_runs()[0].id == run.id

    async def test_prompt_carries_evidence_and_contract(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, *_ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))

        prompt = router.calls[0]["prompt"]
        assert "Issue #5" in prompt
        assert "RESPONSE FORMAT" in prompt
        assert "untrusted issue body" in prompt
        assert router.calls[0]["system"].startswith("You are the fleet's issue triager")

    async def test_balancer_charged_with_actuals(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, balancer, _ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))

        assert balancer.usage("claude-code").tokens == 1500
        assert balancer.usage("claude-code").runs == 1


class TestApply:
    async def test_apply_posts_comment_with_marker_and_labels(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, _, fake = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(5))

        writes = fake.writes()
        assert [w[1] for w in writes] == [
            "/repos/o/r/issues/5/comments",
            "/repos/o/r/issues/5/labels",
        ]
        comment_body = writes[0][2]["body"]
        assert "wtd-fleet:" in comment_body  # dedup marker embedded
        assert "🤖 wtd fleet" in comment_body
        assert run.actions[0].applied is True
        assert run.actions[0].result_url == "https://github.com/o/r/issues/5#c1"

    async def test_already_answered_item_not_recommented(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, _, fake = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        marker = f"<!-- wtd-fleet:{assignment.item.dedup_key} -->"
        fake.route(
            "GET",
            "/repos/o/r/issues/5/comments",
            [{"body": f"earlier answer {marker}", "user": {"login": "wtd-bot"}}],
        )
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(5))

        posted_comments = [
            w for w in fake.writes() if w[1] == "/repos/o/r/issues/5/comments"
        ]
        assert posted_comments == []
        assert run.actions[0].applied is False
        assert "already commented" in (run.actions[0].error or "")

    async def test_write_budget_exhaustion_blocks_actions(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, _, fake = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(0))

        assert fake.writes() == []
        assert all(not a.applied for a in run.actions)
        assert all("write budget" in (a.error or "") for a in run.actions)

    async def test_propose_pr_creates_branch_files_and_draft_pr(self, tmp_path: Path):
        reply = json.dumps(
            {
                "summary": "Wrote the README",
                "actions": [
                    {
                        "type": "propose_pr",
                        "title": "docs: add README",
                        "body": "Adds a README.",
                        "branch": "wtd/readme",
                        "files": [{"path": "README.md", "content": "# Project\n"}],
                    }
                ],
                "discovered": [],
            }
        )
        router = FakeRouter(reply)
        dispatcher, state, _, fake = make_world(tmp_path, router=router)
        fake.route("GET", "/repos/o/r", {"default_branch": "main", "description": "d"})
        fake.route("GET", "/repos/o/r/git/ref/heads/main", {"object": {"sha": "abc"}})
        fake.route("POST", "/repos/o/r/git/refs", {})
        fake.route("GET", "/repos/o/r/contents/README.md", 404)
        fake.route("PUT", "/repos/o/r/contents/README.md", {})
        fake.route("GET", "/repos/o/r/readme", 404)
        fake.route("GET", "/repos/o/r/contents", [])
        fake.route("POST", "/repos/o/r/pulls",
                   {"html_url": "https://github.com/o/r/pull/99"})

        item = WorkItem(
            dedup_key=make_dedup_key("o/r", WorkKind.WRITE_DOCS, "readme"),
            kind=WorkKind.WRITE_DOCS,
            repo="o/r",
            title="Write a README for o/r",
            evidence={"path": "README.md", "missing": True},
        )
        state.enqueue(item)
        assignment = Assignment(item=item, role=builtin_roles()["doc-writer"])

        run = await dispatcher.run(assignment, apply=True, write_budget=CycleBudget(5))

        write_paths = [w[1] for w in fake.writes()]
        assert "/repos/o/r/git/refs" in write_paths
        assert "/repos/o/r/contents/README.md" in write_paths
        assert "/repos/o/r/pulls" in write_paths
        ref_body = next(w[2] for w in fake.writes() if w[1] == "/repos/o/r/git/refs")
        assert ref_body["ref"].startswith("refs/heads/wtd/")
        pr_body = next(w[2] for w in fake.writes() if w[1] == "/repos/o/r/pulls")
        assert pr_body["draft"] is True
        assert run.actions[0].applied is True


class TestFlywheel:
    async def test_discovered_items_enqueued_once(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, *_ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run1 = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run1.discovered == 1
        discovered = [
            i for i in state.items.values() if i.discovered_by == "agent:triage"
        ]
        assert len(discovered) == 1
        assert discovered[0].kind == WorkKind.WRITE_DOCS

        # Re-running the same item rediscovers the same work → deduped.
        assignment.item.status = WorkStatus.QUEUED
        run2 = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run2.discovered == 0
        assert (
            len([i for i in state.items.values() if i.discovered_by == "agent:triage"])
            == 1
        )


class TestFailureHandling:
    async def test_provider_failure_requeues_until_attempts_exhausted(self, tmp_path: Path):
        router = FakeRouter("", fail=ProviderError("claude-code", "boom"))
        dispatcher, state, balancer, _ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run.outcome == RunOutcome.FAILED
        assert assignment.item.status == WorkStatus.QUEUED  # retryable
        assert balancer.usage("claude-code").tokens == 0  # reservation released

        assignment.item.attempts = 2
        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert assignment.item.status == WorkStatus.FAILED  # attempts exhausted

    async def test_rate_limit_cools_down_lane(self, tmp_path: Path):
        router = FakeRouter(
            "", fail=ProviderError("claude-code", "rate limited (retry-after: 60s)")
        )
        dispatcher, state, balancer, _ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert balancer.can_serve("claude-code", 10) is False  # benched
        assert balancer.can_serve("anthropic", 10) is True

    async def test_unparseable_reply_fails_run(self, tmp_path: Path):
        router = FakeRouter("I'm sorry, I can't produce JSON today.")
        dispatcher, state, *_ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run.outcome == RunOutcome.FAILED
        assert "unusable agent reply" in (run.error or "")

    async def test_no_lane_headroom_defers_without_burning_attempt(self, tmp_path: Path):
        router = FakeRouter(triage_reply())
        dispatcher, state, *_ = make_world(
            tmp_path, router=router, lanes=[Lane("claude-code", 10)]
        )
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run.outcome == RunOutcome.SKIPPED
        assert assignment.item.status == WorkStatus.DEFERRED
        assert assignment.item.attempts == 0
        assert router.calls == []  # never reached the model

    async def test_failover_bills_the_lane_that_served(self, tmp_path: Path):
        router = FakeRouter(triage_reply(), provider="anthropic")
        dispatcher, state, balancer, _ = make_world(tmp_path, router=router)
        assignment = triage_assignment()
        state.enqueue(assignment.item)

        run = await dispatcher.run(assignment, apply=False, write_budget=CycleBudget(5))
        assert run.lane == "anthropic"
        assert balancer.usage("anthropic").tokens == 1500
        assert balancer.usage("claude-code").tokens == 0
        # No stuck reservation on the originally-picked lane:
        assert balancer.headroom("claude-code") == 1_000_000
