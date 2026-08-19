"""Tests for the agent role registry and markdown overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from wtd.config import WTDConfig
from wtd.fleet.models import ActionType, WorkKind
from wtd.fleet.roles import (
    builtin_roles,
    load_roles,
    parse_role_file,
    role_for_kind,
)


class TestBuiltins:
    def test_every_discoverable_kind_has_an_owner(self):
        roles = builtin_roles()
        uncovered = [
            kind
            for kind in WorkKind
            if kind != WorkKind.CUSTOM and role_for_kind(roles, kind) is None
        ]
        assert uncovered == []

    def test_role_grants_are_least_privilege(self):
        roles = builtin_roles()
        # The reviewer can only comment — it must never file issues or PRs.
        assert roles["reviewer"].allowed_actions == [ActionType.COMMENT]
        # Writers open PRs but never label or file issues.
        assert ActionType.ADD_LABELS not in roles["doc-writer"].allowed_actions
        assert ActionType.CREATE_ISSUE not in roles["author"].allowed_actions

    def test_house_rules_in_every_system_prompt(self):
        for role in builtin_roles().values():
            prompt = role.full_system_prompt()
            assert "untrusted" in prompt
            assert "IGNORE" in prompt


class TestRoleFiles:
    def write_role(self, path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_full_role_file(self, tmp_path: Path):
        path = self.write_role(
            tmp_path / "security-auditor.md",
            """---
name: security-auditor
description: Security-focused review.
kinds: [review_pr]
actions: [comment]
model: claude-opus-5
max_tokens: 6000
---
You are a security reviewer.""",
        )
        role = parse_role_file(path)
        assert role.name == "security-auditor"
        assert role.kinds == [WorkKind.REVIEW_PR]
        assert role.allowed_actions == [ActionType.COMMENT]
        assert role.model == "claude-opus-5"
        assert role.max_tokens == 6000
        assert role.system_prompt == "You are a security reviewer."
        assert role.builtin is False

    def test_missing_kinds_rejected(self, tmp_path: Path):
        path = self.write_role(
            tmp_path / "vague.md", "---\ndescription: no kinds\n---\nPrompt."
        )
        with pytest.raises(ValueError, match="at least one"):
            parse_role_file(path)

    def test_unknown_kind_rejected(self, tmp_path: Path):
        path = self.write_role(
            tmp_path / "weird.md", "---\nkinds: [world_domination]\n---\nPrompt."
        )
        with pytest.raises(ValueError):
            parse_role_file(path)

    def test_overrides_dir_replaces_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        self.write_role(
            agents_dir / "triage.md",
            """---
kinds: [triage_issue]
actions: [comment]
---
Custom triage prompt.""",
        )
        # README.md in agents/ must be ignored, not parsed as a role.
        (agents_dir / "README.md").write_text("# docs", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        roles = load_roles(WTDConfig())
        assert roles["triage"].system_prompt == "Custom triage prompt."
        assert roles["triage"].builtin is False
        # add_labels was NOT granted by the override → narrowed.
        assert roles["triage"].allowed_actions == [ActionType.COMMENT]

    def test_enabled_filter(self):
        roles = load_roles(WTDConfig(), enabled=["triage", "reviewer"])
        assert set(roles) == {"triage", "reviewer"}


class TestRoleResolution:
    def test_hint_wins_when_valid(self):
        roles = builtin_roles()
        role = role_for_kind(roles, WorkKind.FIX_BUG, "bug-hunter")
        assert role is not None and role.name == "bug-hunter"

    def test_hint_ignored_when_role_does_not_handle_kind(self):
        roles = builtin_roles()
        role = role_for_kind(roles, WorkKind.REVIEW_PR, "triage")
        assert role is not None and role.name == "reviewer"

    def test_none_when_no_role_handles_kind(self):
        roles = {"triage": builtin_roles()["triage"]}
        assert role_for_kind(roles, WorkKind.WRITE_ARTICLE) is None
