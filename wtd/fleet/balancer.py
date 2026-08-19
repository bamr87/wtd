"""Token-capacity load balancing across provider lanes.

Each provider lane (Claude Code subscription, Anthropic API) has a daily
token budget — and the API lane an estimated-USD cap. The balancer:

* rolls usage windows per UTC day,
* picks the first lane (in chain order) with headroom for a run's
  estimated tokens, honouring cooldowns set after rate limits,
* tracks in-flight reservations so concurrent runs can't oversubscribe,
* records actuals to a small JSON file for `wtd fleet budget`.

Budgets are platform-level guardrails, not provider quotas: they keep an
autonomous loop from silently consuming a whole subscription window or an
unbounded API bill.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from wtd.fleet.settings import FleetSettings


@dataclass
class Lane:
    """One provider lane with its daily budget."""

    name: str
    daily_tokens: int  # <= 0 disables the lane
    daily_usd: float | None = None  # optional estimated-spend cap

    @property
    def enabled(self) -> bool:
        return self.daily_tokens > 0


@dataclass
class LaneUsage:
    day: str = ""
    tokens: int = 0
    usd: float = 0.0
    runs: int = 0
    cooldown_until: str | None = None


@dataclass
class LaneSnapshot:
    """Status view of a lane for monitors."""

    name: str
    enabled: bool
    daily_tokens: int
    used_tokens: int
    reserved_tokens: int
    remaining_tokens: int
    used_usd: float
    daily_usd: float | None
    runs: int
    cooling_down: bool


def _today(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


class CapacityBalancer:
    """Pick lanes under budget; persist daily usage."""

    def __init__(
        self,
        lanes: list[Lane],
        path: Path | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ):
        self.lanes = [lane for lane in lanes]
        self.path = path
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._usage: dict[str, LaneUsage] = {}
        self._reserved: dict[str, int] = {}
        if path is not None:
            self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for name, entry in (raw.get("lanes") or {}).items():
            self._usage[name] = LaneUsage(
                day=str(entry.get("day", "")),
                tokens=int(entry.get("tokens", 0)),
                usd=float(entry.get("usd", 0.0)),
                runs=int(entry.get("runs", 0)),
                cooldown_until=entry.get("cooldown_until"),
            )

    def save(self) -> None:
        if self.path is None:
            return
        payload = {
            "updated_at": self._now().isoformat(),
            "lanes": {
                name: {
                    "day": usage.day,
                    "tokens": usage.tokens,
                    "usd": round(usage.usd, 6),
                    "runs": usage.runs,
                    "cooldown_until": usage.cooldown_until,
                }
                for name, usage in self._usage.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------
    def usage(self, lane_name: str) -> LaneUsage:
        """Current-day usage for a lane, rolling the window as needed."""
        today = _today(self._now())
        usage = self._usage.get(lane_name)
        if usage is None or usage.day != today:
            cooldown = usage.cooldown_until if usage else None
            usage = LaneUsage(day=today, cooldown_until=cooldown)
            self._usage[lane_name] = usage
        return usage

    def _lane(self, name: str) -> Lane | None:
        for lane in self.lanes:
            if lane.name == name:
                return lane
        return None

    def _cooling(self, usage: LaneUsage) -> bool:
        if not usage.cooldown_until:
            return False
        try:
            until = datetime.fromisoformat(usage.cooldown_until)
        except ValueError:
            return False
        return self._now() < until

    def headroom(self, lane_name: str) -> int:
        lane = self._lane(lane_name)
        if lane is None or not lane.enabled:
            return 0
        usage = self.usage(lane_name)
        reserved = self._reserved.get(lane_name, 0)
        return max(0, lane.daily_tokens - usage.tokens - reserved)

    def can_serve(self, lane_name: str, est_tokens: int) -> bool:
        lane = self._lane(lane_name)
        if lane is None or not lane.enabled:
            return False
        usage = self.usage(lane_name)
        if self._cooling(usage):
            return False
        if lane.daily_usd is not None and usage.usd >= lane.daily_usd:
            return False
        return self.headroom(lane_name) >= est_tokens

    def pick(self, est_tokens: int) -> str | None:
        """Reserve capacity on the first lane that can serve the run."""
        for lane in self.lanes:
            if self.can_serve(lane.name, est_tokens):
                self._reserved[lane.name] = (
                    self._reserved.get(lane.name, 0) + est_tokens
                )
                return lane.name
        return None

    def release(self, lane_name: str, est_tokens: int) -> None:
        """Return an unused reservation (run failed before/without usage)."""
        current = self._reserved.get(lane_name, 0)
        self._reserved[lane_name] = max(0, current - est_tokens)

    def record(
        self, lane_name: str, *, est_tokens: int, tokens: int, usd: float = 0.0
    ) -> None:
        """Record actual usage for a completed run and drop its reservation."""
        self.release(lane_name, est_tokens)
        usage = self.usage(lane_name)
        usage.tokens += max(0, tokens)
        usage.usd += max(0.0, usd)
        usage.runs += 1

    def cooldown(self, lane_name: str, seconds: int) -> None:
        """Bench a lane (e.g. after a rate limit) for ``seconds``."""
        usage = self.usage(lane_name)
        usage.cooldown_until = (self._now() + timedelta(seconds=seconds)).isoformat()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def snapshot(self) -> list[LaneSnapshot]:
        report: list[LaneSnapshot] = []
        for lane in self.lanes:
            usage = self.usage(lane.name)
            reserved = self._reserved.get(lane.name, 0)
            report.append(
                LaneSnapshot(
                    name=lane.name,
                    enabled=lane.enabled,
                    daily_tokens=lane.daily_tokens,
                    used_tokens=usage.tokens,
                    reserved_tokens=reserved,
                    remaining_tokens=max(0, lane.daily_tokens - usage.tokens - reserved),
                    used_usd=round(usage.usd, 4),
                    daily_usd=lane.daily_usd,
                    runs=usage.runs,
                    cooling_down=self._cooling(usage),
                )
            )
        return report


def default_lanes(settings: "FleetSettings") -> list[Lane]:
    """Lanes in chain order from fleet settings."""
    return [
        Lane(name="claude-code", daily_tokens=settings.claude_code_daily_tokens),
        Lane(
            name="anthropic",
            daily_tokens=settings.anthropic_daily_tokens,
            daily_usd=settings.anthropic_daily_usd,
        ),
    ]
