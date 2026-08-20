"""The house conventions, as executable rules.

Every repository in this fleet already *states* these rules in prose — in
its CLAUDE.md, its AUTOPILOT.md, its workflow comments. Harmonizing means
making them checkable, so a repo drifting from the shared posture is
visible instead of discovered during an incident.

Each rule reads a :class:`~wtd.fleet.manifest.FleetManifest` and returns
findings. Rules are pure: no I/O, no clock, no network — the manifest is
the only input, which keeps them trivially testable and lets the same
rules run against a derived manifest (machine-read) or a declared one.

Severity is about blast radius, not tidiness:

``critical``
    An autonomous loop can act with no way to stop it, or an agent can
    merge its own work. These are the failure modes the fleet's own
    documents call out as unacceptable.
``warning``
    A real gap that has not bitten yet: a missing fallback lane, an
    unbounded loop, a cross-repo token where an ambient one would do.
``info``
    Drift worth knowing about — vocabulary, cadence collisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from wtd.fleet.manifest import FleetManifest, Harness, Lane, LaneKind

SEVERITIES = ("critical", "warning", "info")
_SEVERITY_WEIGHT = {"critical": 25, "warning": 8, "info": 2}


@dataclass
class Finding:
    rule: str
    severity: str
    repo: str
    message: str
    fix: str
    lane: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "repo": self.repo,
            "lane": self.lane,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class AuditReport:
    repo: str
    findings: list[Finding] = field(default_factory=list)
    lanes_checked: int = 0

    @property
    def score(self) -> int:
        """0–100. Starts at 100; each finding costs by severity."""
        penalty = sum(_SEVERITY_WEIGHT.get(f.severity, 0) for f in self.findings)
        return max(0, 100 - penalty)

    @property
    def grade(self) -> str:
        score = self.score
        for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
            if score >= threshold:
                return letter
        return "F"

    def by_severity(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "score": self.score,
            "grade": self.grade,
            "lanes_checked": self.lanes_checked,
            "findings": [f.to_dict() for f in self.findings],
        }


Rule = Callable[[FleetManifest], list[Finding]]

#: Lanes that a human triggers directly are exempt from autonomy rules.
_HUMAN_DRIVEN = {LaneKind.MENTION}


def _autonomous(manifest: FleetManifest) -> list[Lane]:
    return [lane for lane in manifest.lanes if lane.autonomous]


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------
def rule_autonomous_lanes_have_a_switch(manifest: FleetManifest) -> list[Finding]:
    """Every scheduled AI loop needs an off switch a human can reach.

    lifehacker.dev states it plainly — "every AI loop is OFF until its
    ``*_ENABLED`` repo variable is set". A scheduled loop without one
    cannot be stopped except by editing and pushing the workflow, which is
    exactly the wrong tool during an incident.
    """
    out = []
    for lane in _autonomous(manifest):
        if not lane.gated:
            out.append(
                Finding(
                    rule="switch-required",
                    severity="critical",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=(
                        f"scheduled lane '{lane.id}' ({lane.kind.value}) runs on "
                        f"{_cadence(lane)} with no *_ENABLED kill switch"
                    ),
                    fix=(
                        f"gate the job: `if: vars.{_suggest_switch(lane)} == 'true'`, "
                        f"then set that repo variable to arm it"
                    ),
                )
            )
    return out


def rule_agents_never_merge(manifest: FleetManifest) -> list[Finding]:
    """No lane merges its own work. Universal in this fleet's docs."""
    out = []
    for lane in manifest.lanes:
        if not lane.guardrails.never_merges:
            out.append(
                Finding(
                    rule="never-merge",
                    severity="critical",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=f"lane '{lane.id}' appears to merge pull requests itself",
                    fix=(
                        "remove the merge step — the machine proposes, the human "
                        "disposes; use auto-merge with required reviews if the "
                        "repo genuinely wants unattended merges"
                    ),
                )
            )
    return out


def rule_no_direct_writes_to_default_branch(manifest: FleetManifest) -> list[Finding]:
    out = []
    for lane in manifest.lanes:
        if lane.guardrails.writes_directly_to_default_branch:
            out.append(
                Finding(
                    rule="pr-only",
                    severity="critical",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=f"lane '{lane.id}' pushes straight to the default branch",
                    fix=(
                        "open a pull request instead. A bare `git push` to a "
                        "protected branch also fails silently once a ruleset "
                        "lands — the hub lost four loops that way"
                    ),
                )
            )
    return out


def rule_oauth_first_with_fallback(manifest: FleetManifest) -> list[Finding]:
    """Claude auth is OAuth-first, API key as fallback.

    The subscription lane is prepaid capacity; the metered key is the
    overflow. A lane carrying only the API key silently bills per token.
    """
    out = []
    model_harnesses = {Harness.CLAUDE_CODE_ACTION, Harness.CLAUDE_CLI, Harness.ENGINE}
    for lane in manifest.lanes:
        if lane.harness not in model_harnesses:
            continue
        tokens = set(lane.uses_tokens)
        has_oauth = "CLAUDE_CODE_OAUTH_TOKEN" in tokens
        has_key = "ANTHROPIC_API_KEY" in tokens
        if not has_oauth and has_key:
            out.append(
                Finding(
                    rule="oauth-first",
                    severity="warning",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=f"lane '{lane.id}' authenticates with the metered API key only",
                    fix=(
                        "prefer `claude_code_oauth_token: "
                        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` and keep the API "
                        "key as the fallback"
                    ),
                )
            )
        elif has_oauth and not has_key:
            out.append(
                Finding(
                    rule="auth-fallback",
                    severity="info",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=f"lane '{lane.id}' has no fallback lane if OAuth is unavailable",
                    fix=(
                        "add ANTHROPIC_API_KEY as the fallback so an expired "
                        "OAuth token degrades instead of failing the run"
                    ),
                )
            )
    return out


def rule_cross_repo_token_declared(manifest: FleetManifest) -> list[Finding]:
    """A lane writing to other repos needs more than the ambient token.

    ``GITHUB_TOKEN`` cannot write to another repository, and refs it
    pushes fire no workflow events — so a PR opened with it gets no CI.
    """
    out = []
    for lane in manifest.lanes:
        if lane.kind is not LaneKind.FANOUT:
            continue
        if "FLEET_TOKEN" not in lane.uses_tokens:
            out.append(
                Finding(
                    rule="cross-repo-token",
                    severity="warning",
                    repo=manifest.repo,
                    lane=lane.id,
                    message=(
                        f"fan-out lane '{lane.id}' does not use FLEET_TOKEN; the "
                        "ambient GITHUB_TOKEN cannot write to other repos and its "
                        "pushes trigger no CI"
                    ),
                    fix="pass a fine-grained PAT (FLEET_TOKEN) for cross-repo writes",
                )
            )
    return out


def rule_declares_metering(manifest: FleetManifest) -> list[Finding]:
    """An autonomous fleet should declare a ceiling on its own spend."""
    if not _autonomous(manifest):
        return []
    if manifest.metering.to_dict():
        return []
    return [
        Finding(
            rule="metering",
            severity="warning",
            repo=manifest.repo,
            lane=None,
            message=(
                f"{len(_autonomous(manifest))} autonomous lane(s) run with no "
                "declared token/spend ceiling"
            ),
            fix=(
                "add a `metering:` block (daily_token_budget / daily_usd_budget / "
                "max_runs_per_day) so the loop has a bound"
            ),
        )
    ]


def rule_cadence_collisions(manifest: FleetManifest) -> list[Finding]:
    """Loops firing on the same minute contend for runners and rate limits."""
    seen: dict[str, list[str]] = {}
    for lane in manifest.lanes:
        for trigger in lane.triggers:
            if trigger.cron:
                seen.setdefault(trigger.cron.strip(), []).append(lane.id)
    return [
        Finding(
            rule="cadence-collision",
            severity="info",
            repo=manifest.repo,
            lane=None,
            message=f"lanes {', '.join(sorted(ids))} all fire at cron '{cron}'",
            fix="stagger the schedules so they do not contend for runners",
        )
        for cron, ids in sorted(seen.items())
        if len(ids) > 1
    ]


ALL_RULES: tuple[Rule, ...] = (
    rule_autonomous_lanes_have_a_switch,
    rule_agents_never_merge,
    rule_no_direct_writes_to_default_branch,
    rule_oauth_first_with_fallback,
    rule_cross_repo_token_declared,
    rule_declares_metering,
    rule_cadence_collisions,
)


# ----------------------------------------------------------------------
def _cadence(lane: Lane) -> str:
    crons = [t.cron for t in lane.triggers if t.cron]
    return f"cron {crons[0]}" if crons else "a schedule"


def _suggest_switch(lane: Lane) -> str:
    import re

    stem = re.sub(r"[^A-Za-z0-9]+", "_", lane.id).strip("_").upper()
    return f"{stem}_ENABLED"


def audit(manifest: FleetManifest, rules: tuple[Rule, ...] = ALL_RULES) -> AuditReport:
    """Run every rule against one manifest."""
    report = AuditReport(repo=manifest.repo, lanes_checked=len(manifest.lanes))
    for rule in rules:
        report.findings.extend(rule(manifest))
    order = {sev: i for i, sev in enumerate(SEVERITIES)}
    report.findings.sort(key=lambda f: (order.get(f.severity, 9), f.rule, f.lane or ""))
    return report


def audit_fleet(manifests: list[FleetManifest]) -> list[AuditReport]:
    """Audit every repo, worst score first."""
    reports = [audit(m) for m in manifests]
    reports.sort(key=lambda r: r.score)
    return reports
