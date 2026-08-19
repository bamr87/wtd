"""Tests for the token-capacity load balancer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from wtd.fleet.balancer import CapacityBalancer, Lane


class Clock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def make_balancer(path: Path | None = None, clock: Clock | None = None) -> CapacityBalancer:
    lanes = [
        Lane(name="claude-code", daily_tokens=1000),
        Lane(name="anthropic", daily_tokens=500, daily_usd=5.0),
    ]
    return CapacityBalancer(lanes, path, now=clock or Clock())


class TestLanePicking:
    def test_prefers_first_lane_in_chain_order(self):
        balancer = make_balancer()
        assert balancer.pick(100) == "claude-code"

    def test_spills_to_fallback_when_primary_exhausted(self):
        balancer = make_balancer()
        balancer.record("claude-code", est_tokens=0, tokens=950)
        assert balancer.pick(100) == "anthropic"

    def test_returns_none_when_all_lanes_exhausted(self):
        balancer = make_balancer()
        balancer.record("claude-code", est_tokens=0, tokens=1000)
        balancer.record("anthropic", est_tokens=0, tokens=500)
        assert balancer.pick(100) is None

    def test_reservations_prevent_oversubscription(self):
        balancer = make_balancer()
        assert balancer.pick(600) == "claude-code"  # reserves 600 of 1000
        # A second 600-token run doesn't fit the primary lane anymore.
        assert balancer.pick(600) is None or balancer.pick(600) == "anthropic"

    def test_release_returns_reservation(self):
        balancer = make_balancer()
        assert balancer.pick(900) == "claude-code"
        assert balancer.pick(900) != "claude-code"
        balancer.release("claude-code", 900)
        assert balancer.pick(900) == "claude-code"

    def test_disabled_lane_never_picked(self):
        balancer = CapacityBalancer([Lane(name="claude-code", daily_tokens=0)])
        assert balancer.pick(1) is None


class TestAccounting:
    def test_record_clears_reservation_and_adds_usage(self):
        balancer = make_balancer()
        lane = balancer.pick(400)
        balancer.record(lane, est_tokens=400, tokens=250, usd=0.01)
        usage = balancer.usage("claude-code")
        assert usage.tokens == 250
        assert usage.runs == 1
        assert balancer.headroom("claude-code") == 750

    def test_usd_cap_blocks_lane(self):
        balancer = make_balancer()
        balancer.record("anthropic", est_tokens=0, tokens=10, usd=5.0)
        assert balancer.can_serve("anthropic", 10) is False

    def test_day_rollover_resets_usage(self):
        clock = Clock()
        balancer = make_balancer(clock=clock)
        balancer.record("claude-code", est_tokens=0, tokens=1000)
        assert balancer.pick(100) == "anthropic"
        clock.advance(days=1)
        assert balancer.pick(100) == "claude-code"

    def test_cooldown_benches_lane_until_expiry(self):
        clock = Clock()
        balancer = make_balancer(clock=clock)
        balancer.cooldown("claude-code", seconds=600)
        assert balancer.pick(10) == "anthropic"
        clock.advance(seconds=601)
        assert balancer.pick(10) == "claude-code"

    def test_persistence_roundtrip(self, tmp_path: Path):
        path = tmp_path / "capacity.json"
        clock = Clock()
        balancer = make_balancer(path, clock)
        balancer.record("claude-code", est_tokens=0, tokens=123, usd=0.5)
        balancer.save()

        reloaded = make_balancer(path, clock)
        assert reloaded.usage("claude-code").tokens == 123
        assert reloaded.usage("claude-code").usd == 0.5


class TestSnapshot:
    def test_snapshot_shape(self):
        balancer = make_balancer()
        balancer.pick(100)
        snapshot = {s.name: s for s in balancer.snapshot()}
        assert snapshot["claude-code"].reserved_tokens == 100
        assert snapshot["claude-code"].remaining_tokens == 900
        assert snapshot["anthropic"].daily_usd == 5.0
