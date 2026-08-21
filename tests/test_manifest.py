"""Tests for the shared fleet manifest spec."""

from __future__ import annotations

from pathlib import Path

import pytest

from wtd.fleet.manifest import (
    SPEC_VERSION,
    FleetManifest,
    Guardrails,
    Harness,
    Lane,
    LaneKind,
    ManifestError,
    Metering,
    TokenSpec,
    Trigger,
    TriggerKind,
    load_manifest,
    parse_manifest,
)


def minimal(**overrides) -> dict:
    raw = {
        "spec_version": SPEC_VERSION,
        "repo": "owner/name",
        "lanes": [
            {
                "id": "nightly",
                "kind": "content",
                "harness": "claude-cli",
                "implementation": ".github/workflows/nightly.yml",
                "switch": "NIGHTLY_ENABLED",
                "triggers": [{"kind": "schedule", "cron": "0 6 * * *"}],
            }
        ],
    }
    raw.update(overrides)
    return raw


class TestParsing:
    def test_round_trip(self):
        manifest = parse_manifest(minimal())
        again = parse_manifest(manifest.to_dict())
        assert again.repo == "owner/name"
        assert again.lanes[0].id == "nightly"
        assert again.lanes[0].kind is LaneKind.CONTENT
        assert again.lanes[0].harness is Harness.CLAUDE_CLI
        assert again.lanes[0].switch == "NIGHTLY_ENABLED"

    def test_missing_spec_version_rejected(self):
        raw = minimal()
        del raw["spec_version"]
        with pytest.raises(ManifestError, match="spec_version"):
            parse_manifest(raw)

    def test_future_spec_version_rejected(self):
        with pytest.raises(ManifestError, match="unsupported spec_version"):
            parse_manifest(minimal(spec_version="fleet/v99"))

    def test_repo_must_be_a_slug(self):
        for bad in ("nameonly", "a/b/c", "/x", "x/"):
            with pytest.raises(ManifestError, match="owner/name slug"):
                parse_manifest(minimal(repo=bad))

    def test_duplicate_lane_ids_rejected(self):
        raw = minimal()
        raw["lanes"] = raw["lanes"] * 2
        with pytest.raises(ManifestError, match="duplicate lane"):
            parse_manifest(raw)

    def test_unknown_kind_rejected(self):
        raw = minimal()
        raw["lanes"][0]["kind"] = "world-domination"
        with pytest.raises(ManifestError):
            parse_manifest(raw)

    def test_lane_without_id_rejected(self):
        raw = minimal()
        del raw["lanes"][0]["id"]
        with pytest.raises(ManifestError, match="missing an 'id'"):
            parse_manifest(raw)

    def test_yaml_file_round_trip(self, tmp_path: Path):
        manifest = parse_manifest(minimal())
        target = tmp_path / "fleet.manifest.yml"
        target.write_text(manifest.to_yaml(), encoding="utf-8")
        assert load_manifest(target).repo == "owner/name"

    def test_invalid_yaml_reports_path(self, tmp_path: Path):
        target = tmp_path / "fleet.manifest.yml"
        target.write_text("spec_version: [unclosed", encoding="utf-8")
        with pytest.raises(ManifestError, match="invalid YAML"):
            load_manifest(target)


class TestSemantics:
    def test_autonomous_requires_schedule(self):
        scheduled = Lane(
            id="a", kind=LaneKind.CONTENT, harness=Harness.CLAUDE_CLI,
            implementation="x", triggers=[Trigger(TriggerKind.SCHEDULE, cron="0 6 * * *")],
        )
        manual = Lane(
            id="b", kind=LaneKind.CONTENT, harness=Harness.CLAUDE_CLI,
            implementation="x", triggers=[Trigger(TriggerKind.DISPATCH)],
        )
        assert scheduled.autonomous is True
        assert manual.autonomous is False

    def test_mention_lane_is_never_autonomous(self):
        """A human typing @claude is a human in the loop, even on a cron."""
        lane = Lane(
            id="claude", kind=LaneKind.MENTION, harness=Harness.CLAUDE_CODE_ACTION,
            implementation="x", triggers=[Trigger(TriggerKind.SCHEDULE, cron="0 * * * *")],
        )
        assert lane.scheduled is True
        assert lane.autonomous is False

    def test_ungated_and_switch_helpers(self):
        manifest = parse_manifest(minimal())
        assert manifest.ungated_lanes == []
        assert manifest.switches() == ["NIGHTLY_ENABLED"]
        assert manifest.lane("nightly") is not None
        assert manifest.lane("nope") is None

    def test_guardrails_default_to_never_merging(self):
        assert Guardrails().never_merges is True

    def test_metering_omits_unset_values(self):
        assert Metering().to_dict() == {}
        assert Metering(daily_usd_budget=5.0).to_dict() == {"daily_usd_budget": 5.0}

    def test_token_lookup(self):
        manifest = FleetManifest(
            repo="o/n", tokens=[TokenSpec(name="FLEET_TOKEN", scope="fleet")]
        )
        assert manifest.token("FLEET_TOKEN") is not None
        assert manifest.token("MISSING") is None

    def test_yaml_carries_a_pointer_to_the_spec(self):
        text = parse_manifest(minimal()).to_yaml()
        assert SPEC_VERSION in text
        assert "docs/FLEET-SPEC.md" in text
