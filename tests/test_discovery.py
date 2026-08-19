"""Tests for deterministic work discovery (no network — MockTransport)."""

from __future__ import annotations

import base64

import pytest

from tests.helpers_github import FakeGitHub, issue, pull, workflow_run
from wtd.core.models import TodoPriority
from wtd.fleet.discovery import RepoDiscovery
from wtd.fleet.models import WorkKind
from wtd.fleet.settings import FleetSettings, RepoConfig, ScanConfig


def make_discovery(fake: FakeGitHub, *, scan: ScanConfig | None = None,
                   articles: bool = False, self_login: str | None = "wtd-bot"):
    repo = RepoConfig(slug="o/r", articles=articles)
    settings = FleetSettings(repos=[repo], scan=scan or ScanConfig())
    return RepoDiscovery(fake.client, repo, settings, self_login=self_login)


def b64_readme(text: str) -> dict:
    return {
        "content": base64.b64encode(text.encode()).decode(),
        "encoding": "base64",
    }


@pytest.fixture
def fake() -> FakeGitHub:
    fg = FakeGitHub()
    # Default empty world; individual tests override routes.
    fg.route("GET", "/repos/o/r", {"default_branch": "main", "description": "d"})
    fg.route("GET", "/repos/o/r/issues", [])
    fg.route("GET", "/repos/o/r/pulls", [])
    fg.route("GET", "/repos/o/r/actions/runs", {"workflow_runs": []})
    fg.route("GET", "/repos/o/r/readme", b64_readme("# Project\n" + "x" * 400))
    return fg


class TestIssueScanning:
    async def test_unlabeled_issue_becomes_triage(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/issues", [issue(1, "Something is odd")])
        items = await make_discovery(fake).scan_issues()
        assert len(items) == 1
        assert items[0].kind == WorkKind.TRIAGE_ISSUE
        assert items[0].role_hint == "triage"
        assert items[0].evidence["number"] == 1

    async def test_bug_labeled_issue_becomes_fix_bug_high_priority(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/issues", [issue(2, "Crash on save", labels=["bug"])])
        items = await make_discovery(fake).scan_issues()
        assert items[0].kind == WorkKind.FIX_BUG
        assert items[0].priority == TodoPriority.HIGH
        assert items[0].role_hint == "bug-hunter"

    async def test_labeled_non_bug_issue_not_queued(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/issues", [issue(3, "Idea", labels=["enhancement"])])
        assert await make_discovery(fake).scan_issues() == []

    async def test_bot_and_self_authors_skipped(self, fake: FakeGitHub):
        fake.route(
            "GET",
            "/repos/o/r/issues",
            [
                issue(4, "From a bot", user="dependabot[bot]", user_type="Bot"),
                issue(5, "From ourselves", user="wtd-bot"),
                issue(6, "From a human"),
            ],
        )
        items = await make_discovery(fake).scan_issues()
        assert [i.evidence["number"] for i in items] == [6]

    async def test_same_issue_rescanned_same_dedup_key(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/issues", [issue(7, "Original title")])
        first = (await make_discovery(fake).scan_issues())[0]
        fake.route("GET", "/repos/o/r/issues", [issue(7, "Edited title")])
        second = (await make_discovery(fake).scan_issues())[0]
        assert first.dedup_key == second.dedup_key


class TestPullScanning:
    async def test_open_pr_becomes_review_item(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/pulls", [pull(11, "Add feature")])
        items = await make_discovery(fake).scan_pulls()
        assert items[0].kind == WorkKind.REVIEW_PR
        assert items[0].role_hint == "reviewer"

    async def test_draft_and_own_prs_skipped(self, fake: FakeGitHub):
        fake.route(
            "GET",
            "/repos/o/r/pulls",
            [pull(12, "WIP", draft=True), pull(13, "Ours", user="wtd-bot"), pull(14, "OK")],
        )
        items = await make_discovery(fake).scan_pulls()
        assert [i.evidence["number"] for i in items] == [14]

    async def test_bot_prs_still_reviewed(self, fake: FakeGitHub):
        # Dependabot PRs deserve review; only self-authored are skipped.
        fake.route(
            "GET",
            "/repos/o/r/pulls",
            [pull(15, "Bump dep", user="dependabot[bot]", user_type="Bot")],
        )
        items = await make_discovery(fake).scan_pulls()
        assert len(items) == 1
        assert items[0].evidence["is_bot_author"] is True


class TestCiScanning:
    async def test_latest_failure_per_workflow_queued(self, fake: FakeGitHub):
        fake.route(
            "GET",
            "/repos/o/r/actions/runs",
            {
                "workflow_runs": [
                    workflow_run(30, ".github/workflows/ci.yml", "failure"),
                    workflow_run(29, ".github/workflows/ci.yml", "success"),
                    workflow_run(28, ".github/workflows/deploy.yml", "success"),
                ]
            },
        )
        items = await make_discovery(fake).scan_ci()
        assert len(items) == 1
        assert items[0].kind == WorkKind.INVESTIGATE_CI
        assert items[0].evidence["workflow_path"] == ".github/workflows/ci.yml"

    async def test_recovered_workflow_not_queued(self, fake: FakeGitHub):
        fake.route(
            "GET",
            "/repos/o/r/actions/runs",
            {
                "workflow_runs": [
                    workflow_run(31, ".github/workflows/ci.yml", "success"),
                    workflow_run(30, ".github/workflows/ci.yml", "failure"),
                ]
            },
        )
        assert await make_discovery(fake).scan_ci() == []


class TestDocsScanning:
    async def test_missing_readme_queued(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/readme", 404)
        items = await make_discovery(fake).scan_docs()
        assert items[0].kind == WorkKind.WRITE_DOCS
        assert items[0].evidence["missing"] is True

    async def test_thin_readme_queued(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/readme", b64_readme("# tiny"))
        items = await make_discovery(fake).scan_docs()
        assert items[0].evidence["missing"] is False

    async def test_healthy_readme_not_queued(self, fake: FakeGitHub):
        assert await make_discovery(fake).scan_docs() == []


class TestFullDiscovery:
    async def test_failing_signal_does_not_sink_others(self, fake: FakeGitHub):
        fake.route("GET", "/repos/o/r/issues", [issue(1, "Hello")])
        fake.route("GET", "/repos/o/r/actions/runs", (403, {"message": "Actions disabled"}))
        items = await make_discovery(fake).discover()
        assert any(i.kind == WorkKind.TRIAGE_ISSUE for i in items)

    async def test_article_cadence_weekly_key(self, fake: FakeGitHub):
        discovery = make_discovery(fake, articles=True)
        first = await discovery.scan_article_cadence()
        second = await discovery.scan_article_cadence()
        assert first[0].kind == WorkKind.WRITE_ARTICLE
        assert first[0].dedup_key == second[0].dedup_key  # same week → same item
