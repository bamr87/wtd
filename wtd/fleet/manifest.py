"""The shared fleet manifest — one descriptor every repo publishes.

Across this fleet, several repositories independently grew autonomous AI
loops: scheduled workflows that run Claude, propose changes, and open pull
requests. They converged on the same *conventions* (OAuth-first auth,
default-OFF kill switches, agents never merge) but expressed them in six
different dialects — a hub `_data/fleet.yml`, a `.factory/config.yml`, an
`engine/seed.config.yml`, a `wtd.yml`, and plain workflow YAML.

A manifest is the common denominator: a small, versioned file
(``fleet.manifest.yml``) that describes what a repository's fleet *is* —
its lanes, their switches, the credentials they need, the guardrails they
promise — without prescribing how any of them is implemented. Each repo
keeps its own engine; the manifest is how tooling can see them all at once.

Vocabulary is deliberately borrowed from the prior art rather than
invented:

* ``tokens`` (name/scope/required/purpose/used_by) — from the hub's
  ``_data/fleet.yml`` token contract.
* ``harness``, ``guardrails``, ``metering``, ``cadence`` — from
  irony-works' ``engine/seed.config.yml``.
* ``switch`` (a ``*_ENABLED`` repo variable gating every loop) — from
  lifehacker.dev, which enforces it most strictly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SPEC_VERSION = "fleet/v1"
MANIFEST_FILENAME = "fleet.manifest.yml"


class LaneKind(str, Enum):
    """What a lane is *for* — the SDLC function it performs."""

    CONTENT = "content"  # drafts articles, docs, entries
    TRIAGE = "triage"  # classifies/labels/ranks issues and findings
    REVIEW = "review"  # reviews pull requests
    MAINTENANCE = "maintenance"  # CI health, dependency and drift repair
    ANALYSIS = "analysis"  # measures the fleet; usually read-only
    ORCHESTRATOR = "orchestrator"  # schedules/dispatches other agents
    FANOUT = "fanout"  # propagates standards into other repos
    MENTION = "mention"  # human-invoked (@claude) handler
    OTHER = "other"


class Harness(str, Enum):
    """How a lane actually invokes the model."""

    CLAUDE_CODE_ACTION = "claude-code-action"  # anthropics/claude-code-action
    CLAUDE_CLI = "claude-cli"  # headless `claude -p`
    WTD_FLEET = "wtd-fleet"  # this platform's orchestrator
    ENGINE = "engine"  # a repo-local script that calls a provider
    NONE = "none"


class TriggerKind(str, Enum):
    SCHEDULE = "schedule"
    DISPATCH = "dispatch"
    EVENT = "event"


@dataclass
class Trigger:
    kind: TriggerKind
    cron: str | None = None
    events: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.kind == TriggerKind.SCHEDULE:
            return f"cron {self.cron}"
        if self.kind == TriggerKind.EVENT:
            return ", ".join(self.events) or "event"
        return "manual"


@dataclass
class Guardrails:
    """The promises a lane makes about what it will not do.

    Defaults are the conservative reading: assume a lane may write until
    its manifest says otherwise, but assume it must never merge — that is
    the one rule every repo in this fleet already states out loud.
    """

    never_merges: bool = True
    opens_pull_requests: bool = True
    writes_directly_to_default_branch: bool = False
    #: Paths the lane is permitted to write (empty = unconstrained).
    writable_paths: list[str] = field(default_factory=list)
    #: Marker embedded in artifacts so the loop recognizes its own output.
    dedup_marker: str | None = None
    #: Cap on artifacts produced per run, when the lane declares one.
    max_writes_per_run: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"never_merges": self.never_merges}
        if not self.opens_pull_requests:
            out["opens_pull_requests"] = False
        if self.writes_directly_to_default_branch:
            out["writes_directly_to_default_branch"] = True
        if self.writable_paths:
            out["writable_paths"] = self.writable_paths
        if self.dedup_marker:
            out["dedup_marker"] = self.dedup_marker
        if self.max_writes_per_run is not None:
            out["max_writes_per_run"] = self.max_writes_per_run
        return out


@dataclass
class Lane:
    """One autonomous AI loop in a repository."""

    id: str
    kind: LaneKind
    harness: Harness
    #: Workflow file (or script) that implements the lane.
    implementation: str
    description: str = ""
    triggers: list[Trigger] = field(default_factory=list)
    #: The ``*_ENABLED`` repo variable gating the lane. None = ungated.
    switch: str | None = None
    #: Credential names the lane consumes (matched against `tokens`).
    uses_tokens: list[str] = field(default_factory=list)
    guardrails: Guardrails = field(default_factory=Guardrails)
    #: Files this lane reads or writes as its durable contract.
    state_paths: list[str] = field(default_factory=list)

    @property
    def gated(self) -> bool:
        return bool(self.switch)

    @property
    def scheduled(self) -> bool:
        return any(t.kind == TriggerKind.SCHEDULE for t in self.triggers)

    @property
    def autonomous(self) -> bool:
        """Runs without a human in the loop (scheduled, not mention-driven)."""
        return self.scheduled and self.kind != LaneKind.MENTION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "harness": self.harness.value,
            "implementation": self.implementation,
        }
        if self.description:
            out["description"] = self.description
        if self.triggers:
            out["triggers"] = [
                {k: v for k, v in
                 {"kind": t.kind.value, "cron": t.cron,
                  "events": t.events or None}.items() if v}
                for t in self.triggers
            ]
        out["switch"] = self.switch
        if self.uses_tokens:
            out["uses_tokens"] = self.uses_tokens
        out["guardrails"] = self.guardrails.to_dict()
        if self.state_paths:
            out["state_paths"] = self.state_paths
        return out


@dataclass
class TokenSpec:
    """A credential the fleet needs — shape borrowed from the hub."""

    name: str
    scope: str = "repo"  # repo | fleet | hub
    required: bool = True
    purpose: str = ""
    used_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "scope": self.scope,
                               "required": self.required}
        if self.purpose:
            out["purpose"] = self.purpose
        if self.used_by:
            out["used_by"] = sorted(self.used_by)
        return out


@dataclass
class Metering:
    """Declared spend/volume ceilings, when a repo sets them."""

    daily_token_budget: int | None = None
    daily_usd_budget: float | None = None
    max_runs_per_day: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "daily_token_budget": self.daily_token_budget,
                "daily_usd_budget": self.daily_usd_budget,
                "max_runs_per_day": self.max_runs_per_day,
            }.items()
            if v is not None
        }


@dataclass
class FleetManifest:
    """A repository's fleet, described in the shared vocabulary."""

    repo: str
    spec_version: str = SPEC_VERSION
    summary: str = ""
    #: Where this manifest came from: "declared" (committed) or "derived".
    provenance: str = "declared"
    lanes: list[Lane] = field(default_factory=list)
    tokens: list[TokenSpec] = field(default_factory=list)
    metering: Metering = field(default_factory=Metering)
    #: Agent/skill inventory, for surfacing what personas exist.
    agents: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def autonomous_lanes(self) -> list[Lane]:
        return [lane for lane in self.lanes if lane.autonomous]

    @property
    def ungated_lanes(self) -> list[Lane]:
        return [lane for lane in self.lanes if not lane.gated]

    def lane(self, lane_id: str) -> Lane | None:
        for lane in self.lanes:
            if lane.id == lane_id:
                return lane
        return None

    def switches(self) -> list[str]:
        return sorted({lane.switch for lane in self.lanes if lane.switch})

    def token(self, name: str) -> TokenSpec | None:
        for tok in self.tokens:
            if tok.name == name:
                return tok
        return None

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "spec_version": self.spec_version,
            "repo": self.repo,
            "provenance": self.provenance,
        }
        if self.summary:
            out["summary"] = self.summary
        out["lanes"] = [lane.to_dict() for lane in self.lanes]
        if self.tokens:
            out["tokens"] = [t.to_dict() for t in self.tokens]
        metering = self.metering.to_dict()
        if metering:
            out["metering"] = metering
        if self.agents:
            out["agents"] = sorted(self.agents)
        if self.skills:
            out["skills"] = sorted(self.skills)
        return out

    def to_yaml(self) -> str:
        import yaml

        header = (
            f"# {MANIFEST_FILENAME} — this repository's AI fleet, in the\n"
            f"# shared '{SPEC_VERSION}' vocabulary. See docs/FLEET-SPEC.md in\n"
            "# bamr87/wtd. Generated by `wtd fleet adopt`; edit freely —\n"
            "# hand-written values survive regeneration of other fields.\n"
        )
        body = yaml.safe_dump(self.to_dict(), sort_keys=False, width=100,
                              allow_unicode=True)
        return header + body


class ManifestError(ValueError):
    """A manifest could not be parsed or is invalid."""


def _parse_lane(raw: dict[str, Any], index: int) -> Lane:
    if not isinstance(raw, dict):
        raise ManifestError(f"lane #{index} must be a mapping")
    lane_id = str(raw.get("id") or "").strip()
    if not lane_id:
        raise ManifestError(f"lane #{index} is missing an 'id'")
    try:
        kind = LaneKind(str(raw.get("kind", "other")))
    except ValueError as exc:
        raise ManifestError(f"lane {lane_id!r}: {exc}") from exc
    try:
        harness = Harness(str(raw.get("harness", "none")))
    except ValueError as exc:
        raise ManifestError(f"lane {lane_id!r}: {exc}") from exc

    triggers: list[Trigger] = []
    for t in raw.get("triggers") or []:
        if not isinstance(t, dict):
            raise ManifestError(f"lane {lane_id!r}: trigger must be a mapping")
        try:
            tkind = TriggerKind(str(t.get("kind", "dispatch")))
        except ValueError as exc:
            raise ManifestError(f"lane {lane_id!r}: {exc}") from exc
        triggers.append(
            Trigger(kind=tkind, cron=t.get("cron"),
                    events=[str(e) for e in (t.get("events") or [])])
        )

    g = raw.get("guardrails") or {}
    if not isinstance(g, dict):
        raise ManifestError(f"lane {lane_id!r}: guardrails must be a mapping")
    guardrails = Guardrails(
        never_merges=bool(g.get("never_merges", True)),
        opens_pull_requests=bool(g.get("opens_pull_requests", True)),
        writes_directly_to_default_branch=bool(
            g.get("writes_directly_to_default_branch", False)
        ),
        writable_paths=[str(p) for p in (g.get("writable_paths") or [])],
        dedup_marker=g.get("dedup_marker"),
        max_writes_per_run=g.get("max_writes_per_run"),
    )

    return Lane(
        id=lane_id,
        kind=kind,
        harness=harness,
        implementation=str(raw.get("implementation", "")),
        description=str(raw.get("description", "")),
        triggers=triggers,
        switch=raw.get("switch") or None,
        uses_tokens=[str(t) for t in (raw.get("uses_tokens") or [])],
        guardrails=guardrails,
        state_paths=[str(p) for p in (raw.get("state_paths") or [])],
    )


def parse_manifest(raw: dict[str, Any]) -> FleetManifest:
    """Build a manifest from a parsed mapping, validating as we go."""
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a mapping at the top level")

    version = str(raw.get("spec_version") or "")
    if not version:
        raise ManifestError("manifest is missing 'spec_version'")
    if version != SPEC_VERSION:
        raise ManifestError(
            f"unsupported spec_version {version!r} (this tool speaks {SPEC_VERSION})"
        )
    repo = str(raw.get("repo") or "").strip()
    if repo.count("/") != 1 or not all(p.strip() for p in repo.split("/")):
        raise ManifestError(f"'repo' must be an owner/name slug, got {repo!r}")

    lanes = [_parse_lane(item, i) for i, item in enumerate(raw.get("lanes") or [])]
    seen: set[str] = set()
    for lane in lanes:
        if lane.id in seen:
            raise ManifestError(f"duplicate lane id {lane.id!r}")
        seen.add(lane.id)

    tokens = []
    for t in raw.get("tokens") or []:
        if not isinstance(t, dict) or not t.get("name"):
            raise ManifestError("each token needs a 'name'")
        tokens.append(
            TokenSpec(
                name=str(t["name"]),
                scope=str(t.get("scope", "repo")),
                required=bool(t.get("required", True)),
                purpose=str(t.get("purpose", "")),
                used_by=[str(u) for u in (t.get("used_by") or [])],
            )
        )

    m = raw.get("metering") or {}
    metering = Metering(
        daily_token_budget=m.get("daily_token_budget"),
        daily_usd_budget=m.get("daily_usd_budget"),
        max_runs_per_day=m.get("max_runs_per_day"),
    )

    return FleetManifest(
        repo=repo,
        spec_version=version,
        summary=str(raw.get("summary", "")),
        provenance=str(raw.get("provenance", "declared")),
        lanes=lanes,
        tokens=tokens,
        metering=metering,
        agents=[str(a) for a in (raw.get("agents") or [])],
        skills=[str(s) for s in (raw.get("skills") or [])],
    )


def load_manifest(path: Path) -> FleetManifest:
    """Read and validate a manifest file."""
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return parse_manifest(raw)
    except ManifestError as exc:
        raise ManifestError(f"{path}: {exc}") from exc
