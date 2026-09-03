"""The merge gate: the deterministic half of every merge decision."""

from __future__ import annotations

import pytest

from wtd.fleet.mergegate import (
    CiSummary,
    MergePolicy,
    evaluate_merge,
    latest_review_states,
    summarize_ci,
)

GREEN = CiSummary(total=2, passed=2)
OPEN_POLICY = MergePolicy(enabled=True, require_review_approval=False)


def pull(**overrides):
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
        "head": {"sha": "a" * 40},
        "html_url": "https://github.com/o/r/pull/7",
    }
    base.update(overrides)
    return base


class TestSummarizeCi:
    def test_all_completed_and_successful_is_green(self):
        summary = summarize_ci(
            [
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "skipped"},
            ],
            {"state": "success", "statuses": [{"context": "deploy", "state": "success"}]},
        )
        assert summary.green
        assert summary.total == 3

    def test_a_running_check_is_pending_not_passing(self):
        summary = summarize_ci([{"name": "test", "status": "in_progress"}])
        assert not summary.green
        assert summary.pending == ("test",)

    def test_a_failure_is_named_with_its_conclusion(self):
        summary = summarize_ci(
            [{"name": "test", "status": "completed", "conclusion": "failure"}]
        )
        assert not summary.green
        assert summary.failing == ("test: failure",)

    def test_action_required_blocks(self):
        # GitHub reports 'action_required' for a workflow awaiting approval:
        # nothing actually ran, so nothing was verified.
        summary = summarize_ci(
            [{"name": "CI", "status": "completed", "conclusion": "action_required"}]
        )
        assert not summary.green

    def test_no_signal_at_all_is_not_green(self):
        summary = summarize_ci([], {"state": "pending", "statuses": []})
        assert summary.total == 0
        assert not summary.green
        assert summary.describe() == "no checks reported"

    def test_failing_commit_status_blocks(self):
        summary = summarize_ci(
            [], {"state": "failure", "statuses": [{"context": "cov", "state": "error"}]}
        )
        assert summary.failing == ("cov: error",)


class TestReviewStates:
    def test_latest_decisive_review_per_login_wins(self):
        states = latest_review_states(
            [
                {"user": {"login": "ann"}, "state": "CHANGES_REQUESTED"},
                {"user": {"login": "ann"}, "state": "APPROVED"},
                {"user": {"login": "bo"}, "state": "COMMENTED"},
            ]
        )
        assert states == {"ann": "APPROVED"}


class TestEvaluateMerge:
    def test_green_reviewed_and_open_merges(self):
        decision = evaluate_merge(pull(), policy=OPEN_POLICY, ci=GREEN, repo="o/r")
        assert decision.allowed
        assert decision.blockers == []
        assert decision.method == "squash"

    def test_disabled_policy_refuses_everything(self):
        decision = evaluate_merge(pull(), policy=MergePolicy(), ci=GREEN)
        assert not decision.allowed
        assert "not enabled" in decision.reason

    def test_red_ci_blocks(self):
        red = CiSummary(total=1, failing=("test: failure",))
        decision = evaluate_merge(pull(), policy=OPEN_POLICY, ci=red)
        assert not decision.allowed
        assert "CI is not green" in decision.reason

    def test_draft_blocks(self):
        decision = evaluate_merge(pull(draft=True), policy=OPEN_POLICY, ci=GREEN)
        assert not decision.allowed
        assert "draft" in decision.reason

    def test_conflicts_block(self):
        decision = evaluate_merge(
            pull(mergeable=False, mergeable_state="dirty"),
            policy=OPEN_POLICY,
            ci=GREEN,
        )
        assert not decision.allowed
        assert "conflicts" in decision.reason

    def test_uncomputed_mergeability_is_a_retry_not_a_pass(self):
        decision = evaluate_merge(
            pull(mergeable=None, mergeable_state="unknown"),
            policy=OPEN_POLICY,
            ci=GREEN,
        )
        assert not decision.allowed
        assert "not computed" in decision.reason or "has not computed" in decision.reason

    def test_changes_requested_blocks(self):
        decision = evaluate_merge(
            pull(),
            policy=OPEN_POLICY,
            ci=GREEN,
            reviews=[{"user": {"login": "ann"}, "state": "CHANGES_REQUESTED"}],
        )
        assert not decision.allowed
        assert "changes requested by ann" in decision.reason

    def test_blocked_label_blocks(self):
        decision = evaluate_merge(
            pull(labels=[{"name": "DO-NOT-MERGE"}]), policy=OPEN_POLICY, ci=GREEN
        )
        assert not decision.allowed
        assert "do-not-merge" in decision.reason

    def test_fleet_authored_is_refused_by_default(self):
        decision = evaluate_merge(
            pull(), policy=OPEN_POLICY, ci=GREEN, fleet_authored=True
        )
        assert not decision.allowed
        assert "allow_fleet_authored" in decision.reason

    def test_fleet_authored_merges_only_when_written_down(self):
        policy = MergePolicy(
            enabled=True, require_review_approval=False, allow_fleet_authored=True
        )
        decision = evaluate_merge(pull(), policy=policy, ci=GREEN, fleet_authored=True)
        assert decision.allowed

    def test_approval_is_pinned_to_the_reviewed_commit(self):
        policy = MergePolicy(enabled=True)
        assert evaluate_merge(
            pull(), policy=policy, ci=GREEN, approved_sha="a" * 40
        ).allowed
        moved = evaluate_merge(pull(), policy=policy, ci=GREEN, approved_sha="b" * 40)
        assert not moved.allowed
        assert "head moved" in moved.reason

    def test_missing_approval_blocks_when_required(self):
        decision = evaluate_merge(pull(), policy=MergePolicy(enabled=True), ci=GREEN)
        assert not decision.allowed
        assert "no standing fleet review approval" in decision.reason

    def test_every_blocker_is_reported_not_just_the_first(self):
        decision = evaluate_merge(
            pull(draft=True, mergeable=False, mergeable_state="dirty"),
            policy=MergePolicy(enabled=True),
            ci=CiSummary(total=1, failing=("test: failure",)),
        )
        assert len(decision.blockers) >= 4

    @pytest.mark.parametrize("method,expected", [("rebase", "rebase"), ("bogus", "squash")])
    def test_unknown_merge_method_falls_back_to_squash(self, method, expected):
        policy = MergePolicy(enabled=True, method=method, require_review_approval=False)
        assert evaluate_merge(pull(), policy=policy, ci=GREEN).method == expected
