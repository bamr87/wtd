"""Terminal rendering for the cross-repo harmonization surfaces."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from wtd.fleet.conventions import AuditReport
from wtd.fleet.manifest import FleetManifest, LaneKind
from wtd.ui.output import console

_GRADE_STYLE = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "bold red"}
_SEVERITY_STYLE = {"critical": "bold red", "warning": "yellow", "info": "dim"}
_KIND_ICON = {
    LaneKind.CONTENT: "✍",
    LaneKind.TRIAGE: "🏷",
    LaneKind.REVIEW: "👁",
    LaneKind.MAINTENANCE: "🔧",
    LaneKind.ANALYSIS: "📊",
    LaneKind.ORCHESTRATOR: "🚁",
    LaneKind.FANOUT: "📡",
    LaneKind.MENTION: "💬",
    LaneKind.OTHER: "•",
}


def print_fleet_map(manifests: list[FleetManifest]) -> None:
    """The unified view: every AI lane across every repo, in one table."""
    console.header("🗺  Fleet Map")

    total_lanes = sum(len(m.lanes) for m in manifests)
    autonomous = sum(len(m.autonomous_lanes) for m in manifests)
    ungated = sum(1 for m in manifests for lane in m.autonomous_lanes if not lane.gated)
    console.print(
        f"{len(manifests)} repos · {total_lanes} AI lanes · "
        f"{autonomous} autonomous · "
        + (
            f"[bold red]{ungated} autonomous and ungated[/]"
            if ungated
            else "[green]all autonomous lanes gated[/]"
        )
    )

    for manifest in manifests:
        if not manifest.lanes:
            console.muted(f"{manifest.repo}: no AI lanes detected")
            continue
        table = Table(
            title=f"{manifest.repo}  ({manifest.provenance})",
            show_header=True,
            title_justify="left",
        )
        for col in ("lane", "kind", "harness", "trigger", "switch", "guardrails"):
            table.add_column(col, overflow="fold")
        for lane in manifest.lanes:
            trigger = " · ".join(t.describe() for t in lane.triggers) or "—"
            if lane.switch:
                switch = f"[green]{lane.switch}[/]"
            elif lane.autonomous:
                switch = "[bold red]UNGATED[/]"
            else:
                switch = "[dim]n/a[/]"
            rails = []
            if not lane.guardrails.never_merges:
                rails.append("[bold red]merges[/]")
            if lane.guardrails.writes_directly_to_default_branch:
                rails.append("[bold red]push→main[/]")
            if lane.guardrails.opens_pull_requests:
                rails.append("[green]PR[/]")
            table.add_row(
                f"{_KIND_ICON.get(lane.kind, '•')} {lane.id}",
                lane.kind.value,
                lane.harness.value,
                trigger,
                switch,
                " ".join(rails) or "—",
            )
        console.print(table)


def print_audit(reports: list[AuditReport], *, verbose: bool = False) -> None:
    """Conformance against the shared conventions, worst first."""
    console.header("⚖  Fleet Conventions Audit")

    summary = Table(show_header=True)
    for col in ("repo", "grade", "score", "lanes", "critical", "warning", "info"):
        summary.add_column(col)
    for report in reports:
        style = _GRADE_STYLE.get(report.grade, "white")
        summary.add_row(
            report.repo,
            f"[{style}]{report.grade}[/]",
            str(report.score),
            str(report.lanes_checked),
            str(len(report.by_severity("critical"))) or "0",
            str(len(report.by_severity("warning"))),
            str(len(report.by_severity("info"))),
        )
    console.print(summary)

    for report in reports:
        shown = report.findings if verbose else [
            f for f in report.findings if f.severity != "info"
        ]
        if not shown:
            continue
        lines = []
        for finding in shown:
            style = _SEVERITY_STYLE.get(finding.severity, "white")
            where = f"/{finding.lane}" if finding.lane else ""
            lines.append(
                f"[{style}]{finding.severity:8s}[/] [{finding.rule}]{where}\n"
                f"   {finding.message}\n"
                f"   [dim]fix:[/] {finding.fix}"
            )
        console.print(
            Panel(
                "\n\n".join(lines),
                title=f"{report.repo} — {report.grade} ({report.score}/100)",
                border_style=_GRADE_STYLE.get(report.grade, "white"),
            )
        )

    criticals = sum(len(r.by_severity("critical")) for r in reports)
    if criticals:
        console.warning(
            f"{criticals} critical finding(s): an autonomous loop that cannot be "
            "stopped, or an agent that can merge or push to main."
        )
    else:
        console.success("No critical findings — the fleet holds its own conventions.")


def print_manifest(manifest: FleetManifest) -> None:
    console.print(manifest.to_yaml())
