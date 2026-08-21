"""Tests for the house-convention rules."""

from __future__ import annotations

from wtd.fleet.conventions import (
    audit,
    audit_fleet,
    rule_agents_never_merge,
    rule_autonomous_lanes_have_a_switch,
    rule_cadence_collisions,
    rule_cross_repo_token_declared,
    rule_declares_metering,
    rule_no_direct_writes_to_default_branch,
    rule_oauth_first_with_fallback,
)
from wtd.fleet.manifest import (
    FleetManifest,
    Guardrails,
    Harness,
    Lane,
    LaneKind,
    Metering,
    Trigger,
    TriggerKind,
)


def lane(
    lane_id="nightly",
    kind=LaneKind.CONTENT,
    *,
    cron="0 6 * * *",
    switch="NIGHTLY_ENABLED",
    tokens=("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    guardrails=None,
    harness=Harness.CLAUDE_CLI,
) -> Lane:
    triggers = [Trigger(TriggerKind.SCHEDULE, cron=cron)] if cron else [
        Trigger(TriggerKind.DISPATCH)
    ]
    return Lane(
        id=lane_id,
        kind=kind,
        harness=harness,
        implementation=f".github/workflows/{lane_id}.yml",
        triggers=triggers,
        switch=switch,
        uses_tokens=list(tokens),
        guardrails=guardrails or Guardrails(),
    )


def manifest(*lanes, metering=None) -> FleetManifest:
    return FleetManifest(
        repo="owner/name",
        lanes=list(lanes),
        metering=metering or Metering(daily_token_budget=1_000_000),
    )


class TestSwitchRule:
    def test_ungated_autonomous_lane_is_critical(self):
        findings = rule_autonomous_lanes_have_a_switch(manifest(lane(switch=None)))
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert "NIGHTLY_ENABLED" in findings[0].fix  # suggests the variable name

    def test_gated_lane_passes(self):
        assert rule_autonomous_lanes_have_a_switch(manifest(lane())) == []

    def test_manual_lane_needs_no_switch(self):
        assert rule_autonomous_lanes_have_a_switch(
            manifest(lane(cron=None, switch=None))
        ) == []

    def test_mention_lane_needs_no_switch(self):
        """A human typing @claude is the gate."""
        assert rule_autonomous_lanes_have_a_switch(
            manifest(lane(kind=LaneKind.MENTION, switch=None))
        ) == []


class TestSafetyRules:
    def test_merging_lane_is_critical(self):
        findings = rule_agents_never_merge(
            manifest(lane(guardrails=Guardrails(never_merges=False)))
        )
        assert [f.severity for f in findings] == ["critical"]

    def test_push_to_default_branch_is_critical(self):
        findings = rule_no_direct_writes_to_default_branch(
            manifest(lane(guardrails=Guardrails(writes_directly_to_default_branch=True)))
        )
        assert [f.severity for f in findings] == ["critical"]

    def test_clean_lane_produces_nothing(self):
        m = manifest(lane())
        assert rule_agents_never_merge(m) == []
        assert rule_no_direct_writes_to_default_branch(m) == []


class TestAuthRules:
    def test_api_key_only_warns(self):
        findings = rule_oauth_first_with_fallback(
            manifest(lane(tokens=("ANTHROPIC_API_KEY",)))
        )
        assert [f.rule for f in findings] == ["oauth-first"]
        assert findings[0].severity == "warning"

    def test_oauth_without_fallback_is_info(self):
        findings = rule_oauth_first_with_fallback(
            manifest(lane(tokens=("CLAUDE_CODE_OAUTH_TOKEN",)))
        )
        assert [f.rule for f in findings] == ["auth-fallback"]
        assert findings[0].severity == "info"

    def test_oauth_first_with_fallback_passes(self):
        assert rule_oauth_first_with_fallback(manifest(lane())) == []

    def test_non_model_lane_is_exempt(self):
        assert rule_oauth_first_with_fallback(
            manifest(lane(harness=Harness.WTD_FLEET, tokens=()))
        ) == []


class TestFanoutAndMetering:
    def test_fanout_without_fleet_token_warns(self):
        findings = rule_cross_repo_token_declared(
            manifest(lane(kind=LaneKind.FANOUT, tokens=("GITHUB_TOKEN",)))
        )
        assert [f.rule for f in findings] == ["cross-repo-token"]

    def test_fanout_with_fleet_token_passes(self):
        assert rule_cross_repo_token_declared(
            manifest(lane(kind=LaneKind.FANOUT, tokens=("FLEET_TOKEN",)))
        ) == []

    def test_missing_metering_warns_only_when_autonomous(self):
        assert rule_declares_metering(manifest(lane(), metering=Metering())) != []
        assert rule_declares_metering(
            manifest(lane(cron=None), metering=Metering())
        ) == []

    def test_declared_metering_passes(self):
        assert rule_declares_metering(manifest(lane())) == []


class TestCadence:
    def test_collision_reported(self):
        findings = rule_cadence_collisions(
            manifest(lane("a", cron="0 6 * * *"), lane("b", cron="0 6 * * *"))
        )
        assert [f.severity for f in findings] == ["info"]
        assert "a, b" in findings[0].message

    def test_staggered_schedules_pass(self):
        assert rule_cadence_collisions(
            manifest(lane("a", cron="0 6 * * *"), lane("b", cron="0 7 * * *"))
        ) == []


class TestReport:
    def test_scoring_and_grades(self):
        clean = audit(manifest(lane()))
        assert clean.score == 100 and clean.grade == "A"

        broken = audit(
            manifest(
                lane(switch=None, guardrails=Guardrails(never_merges=False)),
                metering=Metering(),
            )
        )
        assert broken.score < 60 and broken.grade == "F"

    def test_findings_sorted_critical_first(self):
        report = audit(
            manifest(lane(switch=None, tokens=("CLAUDE_CODE_OAUTH_TOKEN",)),
                     metering=Metering())
        )
        severities = [f.severity for f in report.findings]
        assert severities == sorted(
            severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s]
        )

    def test_fleet_audit_orders_worst_first(self):
        good = FleetManifest(repo="o/good", lanes=[lane()],
                             metering=Metering(daily_token_budget=1))
        bad = FleetManifest(repo="o/bad", lanes=[lane(switch=None)])
        reports = audit_fleet([good, bad])
        assert [r.repo for r in reports] == ["o/bad", "o/good"]

    def test_report_serializes(self):
        data = audit(manifest(lane())).to_dict()
        assert set(data) == {"repo", "score", "grade", "lanes_checked", "findings"}
