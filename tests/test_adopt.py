"""Tests for deriving a manifest from real-shaped workflow files.

The fixtures below are distilled from the six repositories this framework
was built against — including the three shapes that produced false
positives on the first audit run.
"""

from __future__ import annotations

from pathlib import Path

from wtd.fleet.adopt import (
    derive_manifest,
    detect_harness,
    detect_kind,
    lane_from_text,
    merge_evidence,
)
from wtd.fleet.manifest import Harness, LaneKind

GATED_CRON = """\
name: Germinate
on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:
jobs:
  grow:
    if: vars.GERMINATE_ENABLED == 'true'
    steps:
      - run: node engine/scripts/germinate.mjs
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - run: gh pr create --title "nursery"
"""

UNGATED_CRON = """\
name: Fleet Pulse
on:
  schedule:
    - cron: "0 6 * * *"
jobs:
  doctor:
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
"""

MENTION_HANDLER = """\
name: Claude
on:
  issue_comment:
    types: [created]
jobs:
  claude:
    if: contains(github.event.comment.body, '@claude')
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
"""

# The prohibition shape: an agent prompt FORBIDDING merges.
PROHIBITION = """\
name: Issue Pipeline
on:
  schedule:
    - cron: "0 8 * * *"
jobs:
  tier:
    steps:
      - uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            - **Never merge a pull request.** Not with `gh pr merge`, not
              with the API, not ever.
"""

# The detector shape: a security scan LOOKING FOR merge commands.
DETECTOR = """\
name: Framework PR Reviewer
on:
  pull_request:
jobs:
  review:
    steps:
      - run: |
          risky="$(grep -nE 'gh pr merge[^|]*--squash' diff.txt || true)"
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      - run: claude -p "review this"
"""

# The real thing: a lane that is actually granted merge capability.
REAL_MERGER = """\
name: Dependency Warden
on:
  workflow_dispatch:
jobs:
  warden:
    steps:
      - run: |
          claude -p "vet the PR" \
            --allowedTools "Bash(gh pr view:*),Bash(gh pr merge:*)"
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
"""

PUSH_TO_MAIN = """\
name: Grow Lineage
on:
  workflow_dispatch:
jobs:
  grow:
    steps:
      - run: claude -p "write the thing"
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      - run: git push origin HEAD:main && echo published
"""

NOT_AN_AI_LANE = """\
name: CI
on: [push]
jobs:
  test:
    steps:
      - run: pytest
"""


class TestHarnessDetection:
    def test_recognises_each_harness(self):
        assert detect_harness(UNGATED_CRON) is Harness.CLAUDE_CODE_ACTION
        assert detect_harness(GATED_CRON) is Harness.ENGINE
        assert detect_harness(DETECTOR) is Harness.CLAUDE_CLI
        assert detect_harness("run: wtd fleet run --apply") is Harness.WTD_FLEET

    def test_non_ai_workflow_is_not_a_lane(self):
        assert detect_harness(NOT_AN_AI_LANE) is Harness.NONE
        assert lane_from_text("ci.yml", NOT_AN_AI_LANE) is None


class TestKindDetection:
    def test_mention_detected_from_the_gating_expression(self):
        assert detect_kind("claude.yml", "Claude", MENTION_HANDLER) is LaneKind.MENTION

    def test_fanout_seeding_a_mention_handler_is_not_a_mention_lane(self):
        """A fan-out carries '@claude' as payload, not as its own trigger."""
        payload = "name: Standardize\njobs:\n  x:\n    steps:\n      - run: |\n" \
                  "          echo 'mention @claude in an issue' > claude.yml\n" \
                  "          claude -p 'seed it'\n"
        assert detect_kind("standardize-fanout.yml", "Standardize", payload) is LaneKind.FANOUT

    def test_orchestrator_and_content_kinds(self):
        assert detect_kind("fleet-loop.yml", "Fleet Loop", "") is LaneKind.ORCHESTRATOR
        assert detect_kind("wire-scout.yml", "Wire Scout", "") is LaneKind.CONTENT
        assert detect_kind("triage.yml", "Triage", "") is LaneKind.TRIAGE


class TestGuardrailInference:
    def test_prohibition_is_not_read_as_merging(self):
        """Regression: an agent prompt forbidding merges said the lane merges."""
        lane = lane_from_text("issue-pipeline.yml", PROHIBITION)
        assert lane is not None
        assert lane.guardrails.never_merges is True

    def test_detector_is_not_read_as_merging(self):
        """Regression: a grep hunting for `gh pr merge` said the lane merges."""
        lane = lane_from_text("framework-pr-reviewer.yml", DETECTOR)
        assert lane is not None
        assert lane.guardrails.never_merges is True

    def test_real_merge_grant_is_caught(self):
        lane = lane_from_text("warden.yml", REAL_MERGER)
        assert lane is not None
        assert lane.guardrails.never_merges is False
        assert merge_evidence(REAL_MERGER), "a finding must be able to cite itself"

    def test_push_to_default_branch_is_caught(self):
        lane = lane_from_text("grow-lineage.yml", PUSH_TO_MAIN)
        assert lane is not None
        assert lane.guardrails.writes_directly_to_default_branch is True

    def test_pull_request_opening_is_detected(self):
        lane = lane_from_text("germinate.yml", GATED_CRON)
        assert lane is not None and lane.guardrails.opens_pull_requests is True


class TestLaneShape:
    def test_switch_and_triggers_and_secrets(self):
        lane = lane_from_text("germinate.yml", GATED_CRON)
        assert lane is not None
        assert lane.switch == "GERMINATE_ENABLED"
        assert lane.autonomous is True
        assert "CLAUDE_CODE_OAUTH_TOKEN" in lane.uses_tokens
        assert any(t.cron == "0 6 * * 1" for t in lane.triggers)
        assert any(t.kind.value == "dispatch" for t in lane.triggers)

    def test_ungated_autonomous_lane(self):
        lane = lane_from_text("fleet-pulse.yml", UNGATED_CRON)
        assert lane is not None
        assert lane.switch is None
        assert lane.autonomous is True

    def test_events_parsed_despite_yaml_truthy_on_key(self):
        """`on:` parses as the boolean True in YAML 1.1 — triggers must survive."""
        lane = lane_from_text("claude.yml", MENTION_HANDLER)
        assert lane is not None
        events = [e for t in lane.triggers for e in t.events]
        assert "issue_comment" in events


class TestDeriveManifest:
    def test_reads_a_repo_tree(self, tmp_path: Path):
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "germinate.yml").write_text(GATED_CRON, encoding="utf-8")
        (wf / "ci.yml").write_text(NOT_AN_AI_LANE, encoding="utf-8")
        (tmp_path / ".claude" / "agents").mkdir(parents=True)
        (tmp_path / ".claude" / "agents" / "scout.md").write_text("x", encoding="utf-8")
        (tmp_path / ".claude" / "skills" / "grow").mkdir(parents=True)

        manifest = derive_manifest(tmp_path, "owner/name")

        assert manifest.provenance == "derived"
        assert [lane.id for lane in manifest.lanes] == ["germinate"]
        assert manifest.agents == ["scout"]
        assert manifest.skills == ["grow"]
        # Token contract is rolled up from the lanes that use each secret.
        oauth = manifest.token("CLAUDE_CODE_OAUTH_TOKEN")
        assert oauth is not None and oauth.used_by == ["germinate"]

    def test_repo_without_workflows_is_empty_not_an_error(self, tmp_path: Path):
        manifest = derive_manifest(tmp_path, "owner/name")
        assert manifest.lanes == []
