"""Integration tests for the orchestrator cycle (fakes only, no network)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers_github import FakeGitHub, issue
from tests.test_dispatcher import FakeRouter
from wtd.config import WTDConfig
from wtd.fleet.balancer import CapacityBalancer, Lane
from wtd.fleet.models import WorkStatus
from wtd.fleet.orchestrator import FleetOrchestrator
from wtd.fleet.settings import FleetSettings, RepoConfig
from wtd.fleet.state import FleetState


def triage_reply() -> str:
    return json.dumps(
        {
            "summary": "Triaged",
            "actions": [{"type": "comment", "body": "hi"}],
            "discovered": [],
        }
    )


def make_orchestrator(
    tmp_path: Path,
    *,
    config: WTDConfig | None = None,
    router: FakeRouter | None = None,
) -> tuple[FleetOrchestrator, FakeGitHub]:
    fake = FakeGitHub()
    fake.route("GET", "/user", {"login": "wtd-bot"})
    fake.route("GET", "/repos/o/r", {"default_branch": "main"})
    fake.route("GET", "/repos/o/r/issues", [issue(1, "Needs triage")])
    fake.route("GET", "/repos/o/r/pulls", [])
    fake.route("GET", "/repos/o/r/actions/runs", {"workflow_runs": []})
    fake.route("GET", "/repos/o/r/readme", {"content": "", "encoding": "base64"})
    fake.route("GET", "/repos/o/r/issues/1/comments", [])
    fake.route("POST", "/repos/o/r/issues/1/comments", {"html_url": "https://x/c"})

    config = config or WTDConfig()
    settings = FleetSettings(repos=[RepoConfig(slug="o/r")])
    settings.scan.docs = False  # keep the world to one signal
    orchestrator = FleetOrchestrator(
        config,
        settings,
        state=FleetState(tmp_path / "fleet").load(),
        github=fake.client,
        router=router or FakeRouter(triage_reply()),
        balancer=CapacityBalancer(
            [Lane("claude-code", 1_000_000), Lane("anthropic", 1_000_000)]
        ),
    )
    return orchestrator, fake


class TestCycle:
    async def test_kill_switch_stops_everything(self, tmp_path: Path):
        orchestrator, fake = make_orchestrator(
            tmp_path, config=WTDConfig(fleet_enabled=False)
        )
        report = await orchestrator.cycle()
        assert report.enabled is False
        assert report.runs == []
        assert fake.requests == []  # not even discovery ran

    async def test_dry_run_cycle_discovers_schedules_and_runs(self, tmp_path: Path):
        orchestrator, fake = make_orchestrator(tmp_path)
        report = await orchestrator.cycle()

        assert report.enabled is True
        assert report.apply is False
        assert report.discovered_new == 1
        assert report.scheduled == 1
        assert report.completed == 1
        assert report.actions_applied == 0
        assert fake.writes() == []

        # State persisted: item done, ledger written, capacity saved.
        reloaded = FleetState(tmp_path / "fleet").load()
        item = next(iter(reloaded.items.values()))
        assert item.status == WorkStatus.DONE
        assert len(reloaded.recent_runs()) == 1

    async def test_apply_without_github_token_downgrades_to_dry_run(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        config = WTDConfig(fleet_apply=True)
        config.github_token = None
        orchestrator, fake = make_orchestrator(tmp_path, config=config)

        report = await orchestrator.cycle()
        assert report.apply is False
        assert any("no GitHub token" in note for note in report.notes)
        assert fake.writes() == []

    async def test_apply_cycle_writes_with_marker(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        config = WTDConfig(fleet_apply=True)
        orchestrator, fake = make_orchestrator(tmp_path, config=config)

        report = await orchestrator.cycle()
        assert report.apply is True
        assert report.actions_applied == 1
        comment = next(w for w in fake.writes() if w[1].endswith("/comments"))
        assert "wtd-fleet:" in comment[2]["body"]

    async def test_second_cycle_is_idempotent(self, tmp_path: Path):
        orchestrator, _ = make_orchestrator(tmp_path)
        first = await orchestrator.cycle()
        assert first.completed == 1
        second = await orchestrator.cycle()
        # The issue is already handled: rediscovered (dedup) but not re-run.
        assert second.discovered_new == 0
        assert second.scheduled == 0

    async def test_loop_runs_bounded_cycles(self, tmp_path: Path):
        orchestrator, _ = make_orchestrator(tmp_path)
        reports = []
        await orchestrator.loop(
            interval_seconds=0, max_cycles=2, on_report=reports.append
        )
        assert len(reports) == 2


class TestRosterRoleAllowlist:
    """The roster's per-repo `roles:` list must actually reach the scheduler.

    The pure filter was never the hard part — the bug this guards against
    is the orchestrator forgetting to pass it, which is exactly how the
    field came to be parsed, documented, and ignored.
    """

    def orchestrator_with(self, tmp_path: Path, roles: list[str]):
        orchestrator, _ = make_orchestrator(tmp_path)
        orchestrator.settings.repos = [RepoConfig(slug="o/r", roles=roles)]
        return orchestrator

    def queue_two_kinds(self, orchestrator) -> None:
        from wtd.fleet.models import WorkItem, WorkKind, make_dedup_key

        for kind, anchor in ((WorkKind.TRIAGE_ISSUE, "i1"), (WorkKind.REVIEW_PR, "p1")):
            orchestrator.state.enqueue(
                WorkItem(
                    dedup_key=make_dedup_key("o/r", kind, anchor),
                    kind=kind,
                    repo="o/r",
                    title=f"{kind.value} {anchor}",
                    evidence={"number": 1},
                )
            )

    def test_plan_honours_the_roster_allowlist(self, tmp_path: Path):
        orchestrator = self.orchestrator_with(tmp_path, ["triage"])
        self.queue_two_kinds(orchestrator)

        plan = orchestrator.plan()

        assert [a.role.name for a in plan.assignments] == ["triage"]
        assert [i.kind.value for i in plan.unroutable] == ["review_pr"]

    def test_an_empty_roster_list_still_means_every_role(self, tmp_path: Path):
        orchestrator = self.orchestrator_with(tmp_path, [])
        self.queue_two_kinds(orchestrator)

        plan = orchestrator.plan()

        assert sorted(a.role.name for a in plan.assignments) == ["reviewer", "triage"]
        assert plan.unroutable == []

    async def test_a_forbidden_role_never_reaches_an_agent(self, tmp_path: Path):
        # End to end: the run ledger, not just the plan, must stay clean.
        orchestrator, _ = make_orchestrator(tmp_path)
        orchestrator.settings.repos = [RepoConfig(slug="o/r", roles=["reviewer"])]
        orchestrator.settings.scan.pulls = False

        report = await orchestrator.cycle()

        # Discovery found the unlabeled issue; the roster forbids triage.
        assert report.discovered_new == 1
        assert report.runs == []
        assert report.unroutable == 1
