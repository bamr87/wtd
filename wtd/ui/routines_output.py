"""
WTD Routine Output - Beautiful terminal output for routines
"""

from datetime import datetime
from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.style import Style

from wtd.core.routines import (
    Routine,
    RoutineFrequency,
    RoutineManager,
    RoutineStatus,
)
from wtd.ui.output import console, STATUS_ICONS, PRIORITY_ICONS, CONTEXT_ICONS


# Routine status icons
ROUTINE_STATUS_ICONS = {
    RoutineStatus.ACTIVE: "🔄",
    RoutineStatus.PAUSED: "⏸️",
    RoutineStatus.COMPLETED: "✅",
    RoutineStatus.OVERDUE: "⚠️",
    RoutineStatus.SNOOZED: "💤",
    RoutineStatus.ARCHIVED: "📦",
}

# Frequency icons
FREQUENCY_ICONS = {
    RoutineFrequency.DAILY: "📅",
    RoutineFrequency.WEEKLY: "📆",
    RoutineFrequency.BIWEEKLY: "🗓️",
    RoutineFrequency.MONTHLY: "📊",
    RoutineFrequency.QUARTERLY: "📈",
    RoutineFrequency.YEARLY: "🎯",
    RoutineFrequency.CUSTOM: "⏱️",
    RoutineFrequency.CONDITIONAL: "⚡",
}


def print_routine_summary(manager: RoutineManager):
    """Print routine summary."""
    console.header("🔄 Routine TODOs")
    
    summary = manager.get_summary()
    
    # Summary table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value", style="cyan")
    
    table.add_row("Total Routines", str(summary["total"]))
    table.add_row("Active", str(summary["active"]))
    table.add_row("Due Now", f"[yellow]{summary['due']}[/]" if summary["due"] else "0")
    table.add_row("Overdue", f"[red]{summary['overdue']}[/]" if summary["overdue"] else "0")
    table.add_row("Need Review", f"[orange1]{summary['need_reevaluation']}[/]" if summary["need_reevaluation"] else "0")
    table.add_row("Avg Effectiveness", f"{summary['average_effectiveness']:.0%}")
    
    console.print(Panel(table, title="Summary", border_style="magenta"))
    
    # By frequency
    if summary["by_frequency"]:
        console.print()
        console.print("[bold]By Frequency:[/]")
        for freq, count in sorted(summary["by_frequency"].items()):
            icon = FREQUENCY_ICONS.get(RoutineFrequency(freq), "•")
            console.print(f"  {icon} {freq}: {count}")


def print_due_routines(routines: list[Routine]):
    """Print routines that are due."""
    if not routines:
        console.info("No routines are currently due. 🎉")
        return
    
    console.print()
    console.print(f"[bold yellow]⏰ {len(routines)} Routine(s) Due:[/]")
    console.print()
    
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", width=3)
    table.add_column("Freq", width=8)
    table.add_column("Title", ratio=2)
    table.add_column("Due", width=15)
    table.add_column("Eff.", width=5)
    table.add_column("Done", width=5)
    
    for routine in routines:
        status_icon = ROUTINE_STATUS_ICONS.get(routine.status, "?")
        if routine.is_overdue():
            status_icon = "⚠️"
        
        freq_icon = FREQUENCY_ICONS.get(routine.schedule.frequency, "•")
        freq_str = f"{freq_icon} {routine.schedule.frequency.value[:6]}"
        
        # Format due time
        if routine.next_due:
            if routine.is_overdue():
                days_overdue = (datetime.now() - routine.next_due).days
                due_str = f"[red]{days_overdue}d overdue[/]"
            else:
                due_str = routine.next_due.strftime("%m/%d %H:%M")
        else:
            due_str = "On trigger"
        
        # Effectiveness color
        eff = routine.effectiveness_score
        if eff >= 0.8:
            eff_style = "green"
        elif eff >= 0.5:
            eff_style = "yellow"
        else:
            eff_style = "red"
        
        table.add_row(
            status_icon,
            freq_str,
            routine.title[:40] + ("..." if len(routine.title) > 40 else ""),
            due_str,
            f"[{eff_style}]{eff:.0%}[/]",
            str(routine.times_completed),
        )
    
    console.print(table)


def print_routines_needing_review(routines: list[Routine]):
    """Print routines that need re-evaluation."""
    if not routines:
        console.success("All routines are performing well! ✨")
        return
    
    console.print()
    console.print(f"[bold orange1]🔍 {len(routines)} Routine(s) Need Review:[/]")
    console.print()
    
    for routine in routines:
        status_icon = ROUTINE_STATUS_ICONS.get(routine.status, "?")
        reason = routine.get_reevaluation_reason()
        
        console.print(f"  {status_icon} [bold]{routine.title}[/]")
        console.print(f"    [dim]Reason: {reason}[/]")
        console.print(f"    [dim]Completed: {routine.times_completed} | Skipped: {routine.times_skipped}[/]")
        console.print()
    
    console.print("[dim]Consider updating, pausing, or removing these routines.[/]")


def print_all_routines(manager: RoutineManager, show_archived: bool = False):
    """Print all routines."""
    routines = list(manager.routines.values())
    
    if not show_archived:
        routines = [r for r in routines if r.status != RoutineStatus.ARCHIVED]
    
    if not routines:
        console.warning("No routines found.")
        console.info("Create routines in TODO/routines.md using [routine:frequency] syntax")
        return
    
    # Group by frequency
    by_freq: dict[RoutineFrequency, list[Routine]] = {}
    for routine in routines:
        freq = routine.schedule.frequency
        if freq not in by_freq:
            by_freq[freq] = []
        by_freq[freq].append(routine)
    
    # Display order
    freq_order = [
        RoutineFrequency.DAILY,
        RoutineFrequency.WEEKLY,
        RoutineFrequency.BIWEEKLY,
        RoutineFrequency.MONTHLY,
        RoutineFrequency.QUARTERLY,
        RoutineFrequency.YEARLY,
        RoutineFrequency.CUSTOM,
        RoutineFrequency.CONDITIONAL,
    ]
    
    for freq in freq_order:
        if freq not in by_freq:
            continue
        
        freq_routines = by_freq[freq]
        icon = FREQUENCY_ICONS.get(freq, "•")
        
        console.print()
        console.print(f"[bold]{icon} {freq.value.upper()} ({len(freq_routines)})[/]")
        console.print()
        
        for routine in freq_routines:
            _print_routine_line(routine)


def _print_routine_line(routine: Routine):
    """Print a single routine line."""
    status_icon = ROUTINE_STATUS_ICONS.get(routine.status, "?")
    priority_icon = PRIORITY_ICONS.get(routine.priority, "")
    context_icon = CONTEXT_ICONS.get(routine.context, "")
    
    # Build status info
    status_parts = []
    
    if routine.is_overdue():
        status_parts.append("[red]OVERDUE[/]")
    elif routine.is_due():
        status_parts.append("[yellow]DUE[/]")
    
    if routine.should_reevaluate():
        status_parts.append("[orange1]REVIEW[/]")
    
    status_str = " ".join(status_parts)
    
    # Effectiveness bar
    eff = routine.effectiveness_score
    eff_bar = "█" * int(eff * 5) + "░" * (5 - int(eff * 5))
    
    line = f"  {status_icon} {priority_icon} {routine.title} {context_icon}"
    if status_str:
        line += f" [{status_str}]"
    
    console.print(line)
    console.print(f"    [dim]Effectiveness: {eff_bar} {eff:.0%} | Done: {routine.times_completed}x[/]")


def print_routine_detail(routine: Routine):
    """Print detailed routine information."""
    console.print()
    console.print(Panel(
        Text(routine.title, style="bold"),
        title=f"{ROUTINE_STATUS_ICONS.get(routine.status, '?')} Routine Detail",
        border_style="magenta",
    ))
    
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", width=20)
    table.add_column("Value")
    
    table.add_row("Status", routine.status.value)
    table.add_row("Frequency", f"{FREQUENCY_ICONS.get(routine.schedule.frequency, '')} {routine.schedule.frequency.value}")
    table.add_row("Priority", f"{PRIORITY_ICONS.get(routine.priority, '')} {routine.priority.value}")
    table.add_row("Context", f"{CONTEXT_ICONS.get(routine.context, '')} {routine.context.value}")
    table.add_row("", "")
    table.add_row("Next Due", routine.next_due.strftime("%Y-%m-%d %H:%M") if routine.next_due else "On trigger")
    table.add_row("Last Completed", routine.last_completed.strftime("%Y-%m-%d %H:%M") if routine.last_completed else "Never")
    table.add_row("", "")
    table.add_row("Times Completed", str(routine.times_completed))
    table.add_row("Times Skipped", str(routine.times_skipped))
    table.add_row("Times Snoozed", str(routine.times_snoozed))
    table.add_row("Effectiveness", f"{routine.effectiveness_score:.0%}")
    table.add_row("", "")
    table.add_row("Created", routine.created_at.strftime("%Y-%m-%d"))
    
    if routine.should_reevaluate():
        table.add_row("", "")
        table.add_row("[orange1]⚠️ Review Needed[/]", routine.get_reevaluation_reason())
    
    console.print(table)
    
    if routine.source:
        console.print()
        console.print(f"[dim]Source: {routine.source.file_path}:{routine.source.line_number}[/]")


def print_sync_result(stats: dict[str, int]):
    """Print routine sync results."""
    console.print()
    console.success("Routines synced from files")
    console.print(f"  Added: [green]{stats['added']}[/]")
    console.print(f"  Updated: [yellow]{stats['updated']}[/]")
    console.print(f"  Unchanged: [dim]{stats['unchanged']}[/]")

