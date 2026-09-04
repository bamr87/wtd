"""Rich terminal output for the fleet subsystem."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.table import Table

from wtd.fleet.models import WorkItem
from wtd.fleet.orchestrator import CycleReport
from wtd.fleet.roles import AgentRole
from wtd.fleet.scheduler import CyclePlan
from wtd.ui.output import console


def _pct(used: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{min(100, round(100 * used / total))}%"


def print_fleet_status(status: dict[str, Any]) -> None:
    console.header("🚁 WTD Fleet Status")

    mode = "[green]APPLY[/]" if status["apply"] else "[yellow]DRY-RUN[/]"
    enabled = "[green]enabled[/]" if status["enabled"] else "[red]DISABLED[/]"
    console.print(f"Fleet: {enabled} · mode: {mode}")

    chain = Table(title="Provider chain", show_header=True)
    chain.add_column("Lane", style="cyan")
    chain.add_column("Available")
    chain.add_column("Detail", overflow="fold")
    for lane in status["provider_chain"]:
        chain.add_row(
            str(lane["provider"]),
            "[green]yes[/]" if lane["available"] else "[red]no[/]",
            str(lane["detail"]),
        )
    console.print(chain)

    roster = status["roster"]
    repos = ", ".join(roster["repos"]) or "[red](empty — set fleet.repos in wtd.yml)[/]"
    console.print(f"Roster: {repos}")
    if roster["config_file"]:
        console.muted(f"config: {roster['config_file']}")

    print_lanes(status["lanes"])

    queue = status["queue"]
    qtable = Table(title=f"Queue ({queue['total']} items)", show_header=True)
    qtable.add_column("By status", style="cyan")
    qtable.add_column("By kind", style="cyan")
    qtable.add_column("By repo", style="cyan")
    fmt = lambda d: "\n".join(f"{k}: {v}" for k, v in sorted(d.items())) or "—"  # noqa: E731
    qtable.add_row(fmt(queue["by_status"]), fmt(queue["by_kind"]), fmt(queue["by_repo"]))
    console.print(qtable)

    recent = status["recent_runs"]
    if recent["runs"]:
        rtable = Table(
            title=f"Recent runs ({recent['completed']} ok / {recent['failed']} failed)",
            show_header=True,
        )
        for col in ("role", "kind", "repo", "outcome", "lane", "tokens", "acts", "found"):
            rtable.add_column(col)
        for run in recent["runs"]:
            outcome_style = {"completed": "green", "failed": "red"}.get(
                run["outcome"], "yellow"
            )
            rtable.add_row(
                run["role"],
                run["kind"],
                run["repo"],
                f"[{outcome_style}]{run['outcome']}[/]"
                + (" (dry)" if run["dry_run"] else ""),
                run["lane"],
                str(run["tokens"]),
                str(run["actions_applied"]),
                str(run["discovered"]),
            )
        console.print(rtable)
    else:
        console.muted("No runs recorded yet — try: wtd fleet run")


def print_lanes(lanes: list[dict[str, Any]]) -> None:
    table = Table(title="Token capacity (today)", show_header=True)
    table.add_column("Lane", style="cyan")
    table.add_column("Used / Budget")
    table.add_column("Load")
    table.add_column("Runs")
    table.add_column("Est. spend")
    table.add_column("State")
    for lane in lanes:
        state = []
        if not lane["enabled"]:
            state.append("[red]disabled[/]")
        if lane["cooling_down"]:
            state.append("[yellow]cooling down[/]")
        spend = (
            f"${lane['used_usd']:.2f}"
            + (f" / ${lane['daily_usd']:.2f}" if lane["daily_usd"] else "")
            if lane["used_usd"] or lane["daily_usd"]
            else "—"
        )
        table.add_row(
            lane["name"],
            f"{lane['used_tokens']:,} / {lane['daily_tokens']:,}",
            _pct(lane["used_tokens"] + lane["reserved_tokens"], lane["daily_tokens"]),
            str(lane["runs"]),
            spend,
            " ".join(state) or "[green]ready[/]",
        )
    console.print(table)


def print_roles(roles: dict[str, AgentRole]) -> None:
    table = Table(title=f"Agent roles ({len(roles)})", show_header=True)
    table.add_column("Role", style="cyan")
    table.add_column("Handles")
    table.add_column("May request")
    table.add_column("Model")
    table.add_column("Origin")
    table.add_column("Purpose", overflow="fold")
    for role in roles.values():
        table.add_row(
            role.name,
            "\n".join(k.value for k in role.kinds),
            "\n".join(a.value for a in role.allowed_actions) or "(read-only)",
            role.model or "(default)",
            "built-in" if role.builtin else "agents/*.md",
            role.description,
        )
    console.print(table)


def print_queue(items: list[WorkItem], *, limit: int = 30) -> None:
    if not items:
        console.muted("Queue is empty — run: wtd fleet discover")
        return
    table = Table(title=f"Work queue ({len(items)} items)", show_header=True)
    for col in ("kind", "repo", "priority", "status", "attempts", "title"):
        table.add_column(col)
    for item in items[:limit]:
        table.add_row(
            item.kind.value,
            item.repo,
            item.priority.value,
            item.status.value,
            str(item.attempts),
            item.title[:70],
        )
    if len(items) > limit:
        console.muted(f"…and {len(items) - limit} more")
    console.print(table)


def print_plan(plan: CyclePlan) -> None:
    if not plan.assignments:
        console.muted("Nothing to schedule this cycle.")
    else:
        table = Table(title=f"Cycle plan ({len(plan.assignments)} runs)", show_header=True)
        for col in ("#", "role", "kind", "repo", "priority", "title"):
            table.add_column(col)
        for idx, assignment in enumerate(plan.assignments, 1):
            table.add_row(
                str(idx),
                assignment.role.name,
                assignment.item.kind.value,
                assignment.item.repo,
                assignment.item.priority.value,
                assignment.item.title[:60],
            )
        console.print(table)
    if plan.overflow:
        console.muted(f"{len(plan.overflow)} routable items wait for a later cycle.")
    if plan.unroutable:
        console.warning(f"{len(plan.unroutable)} items have no enabled role.")


def print_cycle_report(report: CycleReport) -> None:
    if not report.enabled:
        console.warning("Fleet is disabled (WTD_FLEET_ENABLED=false). Nothing ran.")
        return

    mode = "[green]APPLY[/]" if report.apply else "[yellow]DRY-RUN[/]"
    lines = [
        f"mode: {mode}",
        f"discovered: {report.discovered_new} new · scheduled: {report.scheduled} "
        f"(overflow {report.overflow}, unroutable {report.unroutable})",
        f"runs: [green]{report.completed} completed[/] · "
        f"[red]{report.failed} failed[/] · {report.actions_applied} actions applied",
        f"queue pending after cycle: {report.queue_pending}",
    ]
    for note in report.notes:
        lines.append(f"[yellow]note:[/] {note}")
    console.print(Panel("\n".join(lines), title="Fleet cycle", border_style="cyan"))

    if report.runs:
        table = Table(show_header=True)
        for col in ("role", "repo", "outcome", "lane", "tokens", "summary"):
            table.add_column(col, overflow="fold")
        for run in report.runs:
            outcome_style = {"completed": "green", "failed": "red"}.get(
                run.outcome.value, "yellow"
            )
            detail = run.summary or run.error or ""
            table.add_row(
                run.role,
                run.repo,
                f"[{outcome_style}]{run.outcome.value}[/]",
                run.lane or "—",
                f"{run.input_tokens + run.output_tokens:,}",
                detail[:140],
            )
        console.print(table)
        for run in report.runs:
            for action in run.actions:
                if action.applied and action.result_url:
                    console.success(f"{run.role}: {action.type.value} → {action.result_url}")
                elif not action.applied and report.apply and action.error:
                    console.warning(
                        f"{run.role}: {action.type.value} not applied — {action.error}"
                    )


def print_daily_report(report) -> None:
    """The daily harness: what drifted, what got reviewed, what merged."""
    mode = "[green]APPLY[/]" if report.apply else "[yellow]DRY-RUN[/]"
    lines = [
        f"day: {report.day} · mode: {mode}",
        f"docs: {report.docs_queued} of {len(report.docs)} repos need a "
        f"documentation pass",
        f"reviews: {report.reviews_queued} queued of {len(report.reviews)} open "
        f"pull request(s)",
        f"merges: {report.merged} merged of {len(report.merges)} evaluated",
    ]
    for note in report.notes:
        lines.append(f"[yellow]note:[/] {note}")
    console.print(Panel("\n".join(lines), title="Fleet daily", border_style="cyan"))

    if report.docs:
        table = Table(title="Docs drift", show_header=True)
        for col in ("repo", "verdict", "drift", "commits", "readme", "why"):
            table.add_column(col, overflow="fold")
        for check in report.docs:
            assessment = check.assessment
            if check.error:
                verdict = "[red]error[/]"
            elif assessment.needs_update:
                verdict = "[yellow]stale[/]"
            else:
                verdict = "[green]current[/]"
            table.add_row(
                check.repo,
                verdict,
                f"{assessment.drift_days:.0f}d" if assessment.drift_days else "—",
                str(assessment.commits_since_docs or "—"),
                str(assessment.readme_chars if assessment.readme_chars is not None else "none"),
                (check.error or assessment.summary)[:80],
            )
        console.print(table)

    if report.reviews:
        table = Table(
            title=f"Pull requests seen ({len(report.reviews)})", show_header=True
        )
        for col in ("repo", "pr", "head", "draft", "queued", "title"):
            table.add_column(col, overflow="fold")
        for target in report.reviews[:30]:
            table.add_row(
                target.repo,
                f"#{target.number}",
                target.head_sha[:8],
                "yes" if target.draft else "no",
                "[green]yes[/]" if target.queued else f"[dim]{target.reason}[/]",
                target.title[:60],
            )
        if len(report.reviews) > 30:
            console.muted(f"…and {len(report.reviews) - 30} more")
        console.print(table)

    if report.merges:
        print_merge_check(report.merges, require_approval=True)


def print_merge_check(attempts, *, require_approval: bool = False) -> None:
    """The merge gate's verdict per pull request, with its reasons."""
    if not attempts:
        console.muted(
            "No pull requests evaluated — nothing open, or merging is off "
            "for every repo in scope."
        )
        return
    title = f"Merge gate ({len(attempts)} pull request(s))"
    if not require_approval:
        title += " — approval requirement relaxed for inspection"
    table = Table(title=title, show_header=True)
    for col in ("repo", "pr", "head", "CI", "gate", "why"):
        table.add_column(col, overflow="fold")
    for attempt in attempts:
        decision = attempt.decision
        if attempt.merged:
            gate = "[green]MERGED[/]"
        elif decision.allowed:
            gate = "[green]would merge[/]" if attempt.dry_run else "[yellow]allowed[/]"
        else:
            gate = "[yellow]held[/]"
        ci = "[green]green[/]" if decision.ci.green else f"[red]{decision.ci.describe()}[/]"
        table.add_row(
            attempt.repo,
            f"#{attempt.number}",
            decision.head_sha[:8],
            ci,
            gate,
            (attempt.error or decision.reason)[:90],
        )
    console.print(table)
