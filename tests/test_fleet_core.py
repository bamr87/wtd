"""Tests for fleet models, state persistence, and settings loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from wtd.config import WTDConfig
from wtd.fleet.models import (
    AgentRunRecord,
    RunOutcome,
    WorkItem,
    WorkKind,
    WorkStatus,
    make_dedup_key,
    slugify,
)
from wtd.fleet.settings import load_settings
from wtd.fleet.state import FleetState


def make_item(**overrides) -> WorkItem:
    defaults = dict(
        dedup_key=make_dedup_key("o/r", WorkKind.TRIAGE_ISSUE, "issue#1"),
        kind=WorkKind.TRIAGE_ISSUE,
        repo="o/r",
        title="Triage issue: example",
    )
    defaults.update(overrides)
    return WorkItem(**defaults)


class TestDedupKey:
    def test_stable_across_calls(self):
        a = make_dedup_key("o/r", WorkKind.REVIEW_PR, "pr#7")
        b = make_dedup_key("o/r", WorkKind.REVIEW_PR, "pr#7")
        assert a == b

    def test_normalizes_whitespace_and_case(self):
        a = make_dedup_key("o/r", WorkKind.WRITE_DOCS, "  Write   THE Readme ")
        b = make_dedup_key("o/r", WorkKind.WRITE_DOCS, "write the readme")
        assert a == b

    def test_distinct_per_kind_and_repo(self):
        base = make_dedup_key("o/r", WorkKind.FIX_BUG, "issue#1")
        assert base != make_dedup_key("o/r", WorkKind.TRIAGE_ISSUE, "issue#1")
        assert base != make_dedup_key("o/other", WorkKind.FIX_BUG, "issue#1")

    def test_slugify(self):
        assert slugify("Fix the Thing! (v2)") == "fix-the-thing-v2"
        assert slugify("") == "item"


class TestFleetState:
    def test_enqueue_dedups(self, tmp_path: Path):
        state = FleetState(tmp_path / "fleet").load()
        item = make_item()
        assert state.enqueue(item) is True
        assert state.enqueue(make_item()) is False
        assert len(state.items) == 1

    def test_enqueue_refreshes_without_resetting_progress(self, tmp_path: Path):
        state = FleetState(tmp_path / "fleet").load()
        item = make_item()
        state.enqueue(item)
        state.mark(item, WorkStatus.DONE)
        refreshed = make_item(evidence={"comments": 3}, url="https://x/1")
        assert state.enqueue(refreshed) is False
        stored = state.get(item.dedup_key)
        assert stored is not None
        assert stored.status == WorkStatus.DONE  # progress kept
        assert stored.evidence["comments"] == 3  # evidence refreshed
        assert stored.url == "https://x/1"

    def test_roundtrip_persistence(self, tmp_path: Path):
        state_dir = tmp_path / "fleet"
        state = FleetState(state_dir).load()
        state.enqueue(make_item())
        state.save()

        reloaded = FleetState(state_dir).load()
        assert len(reloaded.items) == 1
        item = next(iter(reloaded.items.values()))
        assert item.kind == WorkKind.TRIAGE_ISSUE
        assert item.repo == "o/r"

    def test_pending_respects_attempts_and_status(self, tmp_path: Path):
        state = FleetState(tmp_path / "fleet").load()
        fresh = make_item()
        tired = make_item(dedup_key="o/r:x:tired", attempts=3)
        done = make_item(dedup_key="o/r:x:done", status=WorkStatus.DONE)
        deferred = make_item(dedup_key="o/r:x:deferred", status=WorkStatus.DEFERRED)
        for item in (fresh, tired, done, deferred):
            state.enqueue(item)
        pending_keys = {i.dedup_key for i in state.pending(max_attempts=3)}
        assert pending_keys == {fresh.dedup_key, deferred.dedup_key}

    def test_run_ledger_roundtrip(self, tmp_path: Path):
        state = FleetState(tmp_path / "fleet").load()
        run = AgentRunRecord(
            item_id="abc",
            dedup_key="o/r:triage_issue:x",
            kind=WorkKind.TRIAGE_ISSUE,
            repo="o/r",
            role="triage",
            outcome=RunOutcome.COMPLETED,
        )
        state.record_run(run)
        runs = state.recent_runs()
        assert len(runs) == 1
        assert runs[0].role == "triage"
        assert runs[0].outcome == RunOutcome.COMPLETED

    def test_prune_done_keeps_recent(self, tmp_path: Path):
        state = FleetState(tmp_path / "fleet").load()
        for n in range(10):
            item = make_item(dedup_key=f"o/r:x:{n}", status=WorkStatus.DONE)
            state.enqueue(item)
        removed = state.prune_done(keep=4)
        assert removed == 6
        assert len(state.items) == 4


class TestSettings:
    def test_env_roster(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WTD_FLEET_REPOS", "a/one, b/two")
        cfg = WTDConfig()
        settings = load_settings(cfg)
        assert settings.repo_slugs() == ["a/one", "b/two"]

    def test_yaml_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        config_file = tmp_path / "wtd.yml"
        config_file.write_text(
            """
fleet:
  repos:
    - repo: c/three
      roles: [triage]
      articles: true
    - d/four
  roles_enabled: [triage, reviewer]
  max_runs_per_cycle: 3
  scan:
    ci: false
  budgets:
    anthropic_daily_usd: 2.5
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("WTD_FLEET_REPOS", "a/one")
        monkeypatch.setenv("WTD_FLEET_CONFIG", str(config_file))
        cfg = WTDConfig()
        settings = load_settings(cfg)

        assert settings.repo_slugs() == ["c/three", "d/four"]
        assert settings.repo("c/three").roles == ["triage"]
        assert settings.repo("c/three").articles is True
        assert settings.roles_enabled == ["triage", "reviewer"]
        assert settings.max_runs_per_cycle == 3
        assert settings.scan.ci is False
        assert settings.scan.issues is True  # untouched default
        assert settings.anthropic_daily_usd == 2.5
        assert settings.source_path == config_file

    def test_invalid_repo_slug_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WTD_FLEET_REPOS", "not-a-slug")
        cfg = WTDConfig()
        with pytest.raises(ValueError):
            load_settings(cfg)

    def test_missing_explicit_config_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("WTD_FLEET_CONFIG", str(tmp_path / "nope.yml"))
        cfg = WTDConfig()
        with pytest.raises(FileNotFoundError):
            load_settings(cfg)


class TestConfigAliases:
    def test_unprefixed_secret_aliases(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp-123")
        cfg = WTDConfig()
        assert cfg.claude_code_oauth_token == "oauth-123"
        assert cfg.anthropic_api_key == "sk-ant-123"
        assert cfg.github_token == "ghp-123"

    def test_prefixed_wins_over_unprefixed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "unprefixed")
        monkeypatch.setenv("WTD_ANTHROPIC_API_KEY", "prefixed")
        cfg = WTDConfig()
        assert cfg.anthropic_api_key == "prefixed"
