"""Tests for agent-output parsing and action validation."""

from __future__ import annotations

import json

import pytest

from wtd.fleet.models import ActionType, WorkItem, WorkKind, make_dedup_key
from wtd.fleet.outcome import (
    MAX_ACTIONS_PER_RUN,
    OutcomeError,
    output_contract,
    parse_outcome,
)
from wtd.fleet.roles import builtin_roles


@pytest.fixture
def roles():
    return builtin_roles()


@pytest.fixture
def triage_item() -> WorkItem:
    return WorkItem(
        dedup_key=make_dedup_key("o/r", WorkKind.TRIAGE_ISSUE, "issue#5"),
        kind=WorkKind.TRIAGE_ISSUE,
        repo="o/r",
        title="Triage issue: example",
        evidence={"number": 5},
    )


def reply(actions=None, discovered=None, summary="did the thing") -> str:
    return json.dumps(
        {"summary": summary, "actions": actions or [], "discovered": discovered or []}
    )


class TestParsing:
    def test_plain_json(self, roles, triage_item):
        outcome = parse_outcome(
            reply([{"type": "comment", "body": "hello"}]), roles["triage"], triage_item
        )
        assert outcome.summary == "did the thing"
        assert outcome.actions[0].type == ActionType.COMMENT

    def test_fenced_json(self, roles, triage_item):
        text = "```json\n" + reply() + "\n```"
        outcome = parse_outcome(text, roles["triage"], triage_item)
        assert outcome.summary == "did the thing"

    def test_garbage_raises(self, roles, triage_item):
        with pytest.raises(OutcomeError):
            parse_outcome("I could not decide what to do.", roles["triage"], triage_item)

    def test_non_object_raises(self, roles, triage_item):
        with pytest.raises(OutcomeError):
            parse_outcome("[1, 2, 3]", roles["triage"], triage_item)


class TestActionValidation:
    def test_ungranted_action_rejected(self, roles, triage_item):
        # triage may not propose PRs
        outcome = parse_outcome(
            reply([{"type": "propose_pr", "title": "x", "files": [{"path": "a", "content": "b"}]}]),
            roles["triage"],
            triage_item,
        )
        assert outcome.actions == []
        assert any("not granted" in r for r in outcome.rejected)

    def test_unknown_action_type_rejected(self, roles, triage_item):
        outcome = parse_outcome(
            reply([{"type": "delete_repo"}]), roles["triage"], triage_item
        )
        assert outcome.actions == []
        assert any("unknown action type" in r for r in outcome.rejected)

    def test_empty_comment_rejected(self, roles, triage_item):
        outcome = parse_outcome(
            reply([{"type": "comment", "body": "  "}]), roles["triage"], triage_item
        )
        assert outcome.actions == []

    def test_action_cap_enforced(self, roles, triage_item):
        actions = [{"type": "comment", "body": f"c{n}"} for n in range(6)]
        outcome = parse_outcome(reply(actions), roles["triage"], triage_item)
        assert len(outcome.actions) == MAX_ACTIONS_PER_RUN

    def test_labels_normalized_and_capped(self, roles, triage_item):
        outcome = parse_outcome(
            reply([{"type": "add_labels", "labels": ["bug", "", "x" * 99, "a", "b", "c", "d"]}]),
            roles["triage"],
            triage_item,
        )
        labels = outcome.actions[0].labels
        assert len(labels) <= 5
        assert all(len(lbl) <= 50 for lbl in labels)
        assert "" not in labels


class TestProposePrSafety:
    def make(self, roles, triage_item, files, branch="wtd/docs"):
        doc_item = WorkItem(
            dedup_key=make_dedup_key("o/r", WorkKind.WRITE_DOCS, "readme"),
            kind=WorkKind.WRITE_DOCS,
            repo="o/r",
            title="Write README",
        )
        return parse_outcome(
            reply(
                [{"type": "propose_pr", "title": "docs", "branch": branch, "files": files}]
            ),
            roles["doc-writer"],
            doc_item,
        )

    def test_valid_pr_accepted(self, roles, triage_item):
        outcome = self.make(roles, triage_item, [{"path": "README.md", "content": "# hi"}])
        assert outcome.actions[0].type == ActionType.PROPOSE_PR
        assert outcome.actions[0].branch.startswith("wtd/")

    def test_path_traversal_rejected(self, roles, triage_item):
        for path in ("../evil.md", "docs/../../evil", "/etc/passwd", "a\\b.md"):
            outcome = self.make(roles, triage_item, [{"path": path, "content": "x"}])
            assert outcome.actions == [], path
            assert any("unsafe or forbidden" in r for r in outcome.rejected)

    def test_workflow_writes_rejected(self, roles, triage_item):
        outcome = self.make(
            roles, triage_item, [{"path": ".github/workflows/evil.yml", "content": "x"}]
        )
        assert outcome.actions == []
        assert any("unsafe or forbidden" in r for r in outcome.rejected)

    def test_branch_prefix_forced(self, roles, triage_item):
        outcome = self.make(
            roles, triage_item, [{"path": "README.md", "content": "x"}], branch="main"
        )
        assert outcome.actions[0].branch.startswith("wtd/")

    def test_too_many_files_rejected(self, roles, triage_item):
        files = [{"path": f"f{n}.md", "content": "x"} for n in range(9)]
        outcome = self.make(roles, triage_item, files)
        assert outcome.actions == []


class TestDiscovered:
    def test_valid_discovered_becomes_work_item(self, roles, triage_item):
        outcome = parse_outcome(
            reply(
                discovered=[
                    {
                        "kind": "write_docs",
                        "title": "Document the config module",
                        "priority": "high",
                    }
                ]
            ),
            roles["triage"],
            triage_item,
        )
        assert len(outcome.discovered) == 1
        found = outcome.discovered[0]
        assert found.kind == WorkKind.WRITE_DOCS
        assert found.repo == "o/r"
        assert found.discovered_by == "agent:triage"
        assert found.evidence["parent_item"] == triage_item.dedup_key

    def test_unknown_kind_rejected(self, roles, triage_item):
        outcome = parse_outcome(
            reply(discovered=[{"kind": "launch_missiles", "title": "no"}]),
            roles["triage"],
            triage_item,
        )
        assert outcome.discovered == []

    def test_review_pr_not_agent_discoverable(self, roles, triage_item):
        # PR review comes from the deterministic scanner, not agent whim.
        outcome = parse_outcome(
            reply(discovered=[{"kind": "review_pr", "title": "review #9"}]),
            roles["triage"],
            triage_item,
        )
        assert outcome.discovered == []

    def test_discovered_cap(self, roles, triage_item):
        discovered = [
            {"kind": "write_docs", "title": f"doc {n}"} for n in range(10)
        ]
        outcome = parse_outcome(
            reply(discovered=discovered), roles["triage"], triage_item, max_discovered=3
        )
        assert len(outcome.discovered) == 3

    def test_agents_cannot_declare_critical(self, roles, triage_item):
        outcome = parse_outcome(
            reply(discovered=[{"kind": "fix_bug", "title": "x", "priority": "critical"}]),
            roles["triage"],
            triage_item,
        )
        assert outcome.discovered[0].priority.value == "high"


class TestContract:
    def test_contract_lists_only_granted_shapes(self, roles):
        contract = output_contract(roles["reviewer"])
        assert '"type": "comment"' in contract
        assert "propose_pr" not in contract


class TestMergeAction:
    """merge_pr is a recommendation; validation keeps it honest and narrow."""

    def review_item(self) -> WorkItem:
        return WorkItem(
            dedup_key=make_dedup_key("o/r", WorkKind.REVIEW_PR, "pr#7@abc"),
            kind=WorkKind.REVIEW_PR,
            repo="o/r",
            title="Review PR #7",
            evidence={"number": 7, "head_sha": "a" * 40},
        )

    def test_reviewer_may_request_a_merge_with_a_rationale(self, roles):
        outcome = parse_outcome(
            reply([{"type": "merge_pr", "body": "Docs-only, CI green, low risk."}]),
            roles["reviewer"],
            self.review_item(),
        )
        assert outcome.actions[0].type == ActionType.MERGE_PR
        assert outcome.actions[0].body.startswith("Docs-only")

    def test_a_merge_with_no_rationale_is_rejected(self, roles):
        outcome = parse_outcome(
            reply([{"type": "merge_pr", "body": "   "}]),
            roles["reviewer"],
            self.review_item(),
        )
        assert outcome.actions == []
        assert "rationale" in outcome.rejected[0]

    def test_other_roles_cannot_request_a_merge(self, roles, triage_item):
        outcome = parse_outcome(
            reply([{"type": "merge_pr", "body": "ship it"}]),
            roles["triage"],
            triage_item,
        )
        assert outcome.actions == []
        assert "not granted to role 'triage'" in outcome.rejected[0]

    def test_the_contract_shows_merge_only_to_roles_that_hold_it(self, roles):
        assert "merge_pr" in output_contract(roles["reviewer"])
        assert "merge_pr" not in output_contract(roles["doc-writer"])
