"""
WTD CLI - The main command-line interface

Usage:
    wtd              # Scan current directory and start working
    wtd scan         # Just scan for TODOs
    wtd dashboard    # Open interactive dashboard
    wtd execute      # Execute the next actionable TODO
    wtd status       # Show progress status
"""

import asyncio
import re
from pathlib import Path
from typing import Optional

import typer
from rich.prompt import Confirm, Prompt

from wtd import __version__
from wtd.config import get_config
from wtd.core.agent import WTDAgent
from wtd.core.scanner import TodoScanner
from wtd.core.tree_store import TreeStore, find_repo_root
from wtd.core.workspace import WorkspaceOrchestrator
from wtd.ui.output import (
    console,
    print_banner,
    print_execution_result,
    print_scan_result,
    print_todo_tree,
    print_workspace_setup,
)

# Create the Typer app
app = typer.Typer(
    name="wtd",
    help="WTD – What To Do: The Ultimate Recursive TODO Engine",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def version_callback(value: bool):
    """Print version and exit."""
    if value:
        console.print(f"WTD version {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        "-i/-I",
        help="Run in interactive dashboard mode",
    ),
    auto_execute: bool = typer.Option(
        False,
        "--auto-execute",
        "-x",
        help="Auto-execute tasks without confirmation",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="LLM provider (auto = Claude Code OAuth → Anthropic API; "
        "or claude-code, anthropic, ollama, openai)",
    ),
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-d",
        help="Path to scan (defaults to current directory)",
        exists=True,
        file_okay=True,
        dir_okay=True,
        resolve_path=True,
    ),
):
    """
    🚀 WTD - What To Do

    Turn every TODO into a self-replicating, AI-orchestrated action factory.

    Just run [bold cyan]wtd[/] in any directory to get started.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Default behavior: scan and run
    scan_path = path or Path.cwd()

    print_banner()

    asyncio.run(
        _main_flow(
            scan_path,
            interactive=interactive,
            auto_execute=auto_execute,
            provider=provider,
        )
    )


async def _main_flow(
    path: Path,
    interactive: bool = True,
    auto_execute: bool = False,
    provider: str | None = None,
):
    """Main WTD flow."""
    config = get_config()

    if provider:
        valid_providers = {"auto", "claude-code", "anthropic", "ollama", "openai"}
        if provider not in valid_providers:
            console.error(
                f"Unknown provider '{provider}'. "
                f"Valid: {', '.join(sorted(valid_providers))}"
            )
            return
        config.llm_provider = provider  # type: ignore[assignment]

    # Step 1: Scan for TODOs
    with console.spinner() as progress:
        task = progress.add_task("Scanning for TODOs...", total=None)

        scanner = TodoScanner(path)
        scan_result = await scanner.scan()

        progress.remove_task(task)

    print_scan_result(scan_result)

    if not scan_result.todos:
        console.warning("No TODOs found.")

        if Confirm.ask("Would you like to create a new TODO?"):
            todo_text = Prompt.ask("Enter your TODO")
            # Create a manual TODO
            from wtd.core.models import TodoNode, TodoSource

            manual_todo = TodoNode(
                title=todo_text,
                description=todo_text,
                source=TodoSource(source_type="manual"),
            )
            scan_result.todos = [manual_todo]
        else:
            console.info("Nothing to do. Goodbye!")
            return

    # Step 2: Initialize agent and analyze context
    agent = WTDAgent(config)

    with console.spinner() as progress:
        task = progress.add_task("Analyzing context...", total=None)
        context = await agent.analyze_context(scan_result)
        progress.remove_task(task)

    console.info(f"Detected context: [bold]{context.value}[/]")

    # Step 3: Persist/merge into tree.json (core repo DNA + history)
    repo_root = find_repo_root(path)
    store = TreeStore(repo_root).load()
    current_node_ids = store.merge_scan(scan_result, scan_path=path)
    store.save()

    # Step 4: Initialize TODO tree from tree.json (source of truth)
    tree = store.to_todotree(node_ids=current_node_ids, include_done=True)
    agent.tree = tree
    print_todo_tree(tree)

    # Step 5: Suggest and set up workspace
    workspace_config = await agent.suggest_workspace(context, scan_result.todos)

    if (
        workspace_config.files_to_open
        or workspace_config.terminals
        or workspace_config.browser_urls
    ):
        if Confirm.ask("Set up workspace with suggested configuration?", default=True):
            orchestrator = WorkspaceOrchestrator(path)

            with console.spinner() as progress:
                task = progress.add_task("Setting up workspace...", total=None)
                results = await orchestrator.setup_workspace(workspace_config)
                progress.remove_task(task)

            print_workspace_setup(results)

    # Step 6: Run interactive dashboard or execute
    if interactive:
        console.info("Launching interactive dashboard...")
        console.print()
        console.print("[dim]Press 'q' to quit, '?' for help[/]")

        from wtd.ui.dashboard import run_dashboard

        run_dashboard(tree, store=store)
    else:
        # Execute mode
        next_todo = tree.get_next_actionable()

        while next_todo:
            console.print()
            console.rule(f"Next: {next_todo.title}")

            if auto_execute or Confirm.ask("Execute this TODO?", default=True):
                with console.spinner() as progress:
                    task = progress.add_task(f"Executing: {next_todo.title}", total=None)
                    result = await agent.execute_todo(next_todo)
                    progress.remove_task(task)

                print_execution_result(next_todo, result)

                # Persist any state changes (status/actions/spawned children) back to tree.json
                store.apply_tree(agent.tree, source="cli_execute")
                store.save()

                if result.spawned_todos:
                    # Update tree visualization
                    print_todo_tree(tree)
            else:
                break

            next_todo = tree.get_next_actionable()

        if not next_todo:
            console.success("All TODOs completed! 🎉")


@app.command()
def scan(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan",
        exists=True,
        resolve_path=True,
    ),
):
    """
    🔍 Scan for TODOs in the current directory or specified path.
    """
    scan_path = path or Path.cwd()

    async def _scan():
        scanner = TodoScanner(scan_path)
        result = await scanner.scan()
        print_scan_result(result)

        # Persist scan into tree.json
        repo_root = find_repo_root(scan_path)
        store = TreeStore(repo_root).load()
        store.merge_scan(result, scan_path=scan_path)
        store.save()
        return result

    asyncio.run(_scan())


@app.command()
def dashboard(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan",
        exists=True,
        resolve_path=True,
    ),
):
    """
    📊 Open the interactive TODO dashboard.
    """
    scan_path = path or Path.cwd()

    async def _dashboard():
        # Scan
        scanner = TodoScanner(scan_path)
        scan_result = await scanner.scan()

        # Persist + load tree from tree.json
        repo_root = find_repo_root(scan_path)
        store = TreeStore(repo_root).load()
        node_ids = store.merge_scan(scan_result, scan_path=scan_path)
        store.save()

        tree = store.to_todotree(node_ids=node_ids, include_done=True)

        # Run dashboard
        from wtd.ui.dashboard import run_dashboard

        run_dashboard(tree, store=store)

    asyncio.run(_dashboard())


@app.command()
def execute(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan",
        exists=True,
        resolve_path=True,
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        "-a",
        help="Auto-execute without confirmation",
    ),
):
    """
    ▶️  Execute the next actionable TODO.
    """
    scan_path = path or Path.cwd()

    async def _execute():
        config = get_config()

        # Scan
        scanner = TodoScanner(scan_path)
        scan_result = await scanner.scan()

        if not scan_result.todos:
            console.warning("No TODOs found.")
            return

        # Persist + load tree from tree.json
        repo_root = find_repo_root(scan_path)
        store = TreeStore(repo_root).load()
        node_ids = store.merge_scan(scan_result, scan_path=scan_path)
        store.save()

        agent = WTDAgent(config)
        tree = store.to_todotree(node_ids=node_ids, include_done=True)
        agent.tree = tree

        # Get next actionable
        next_todo = tree.get_next_actionable()

        if not next_todo:
            console.success("No actionable TODOs!")
            return

        console.info(f"Next TODO: {next_todo.title}")

        if auto or Confirm.ask("Execute?", default=True):
            result = await agent.execute_todo(next_todo)
            print_execution_result(next_todo, result)
            store.apply_tree(agent.tree, source="cli_execute_command")
            store.save()

    asyncio.run(_execute())


@app.command()
def status(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to check",
        exists=True,
        resolve_path=True,
    ),
):
    """
    📈 Show TODO progress status.
    """
    scan_path = path or Path.cwd()

    async def _status():
        scanner = TodoScanner(scan_path)
        scan_result = await scanner.scan()

        repo_root = find_repo_root(scan_path)
        store = TreeStore(repo_root).load()
        node_ids = store.merge_scan(scan_result, scan_path=scan_path)
        store.save()

        tree = store.to_todotree(node_ids=node_ids, include_done=True)

        print_todo_tree(tree)

    asyncio.run(_status())


@app.command()
def config(
    show: bool = typer.Option(
        False,
        "--show",
        "-s",
        help="Show current configuration",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Set LLM provider",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Set model name",
    ),
):
    """
    ⚙️  Configure WTD settings.
    """
    cfg = get_config()

    if show or (not provider and not model):
        from rich.table import Table

        table = Table(title="WTD Configuration", show_header=True)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("LLM Provider", cfg.llm_provider)
        table.add_row("Model", cfg.model)
        table.add_row(
            "Claude Code OAuth token",
            "set" if cfg.claude_code_oauth_token else "(not set — uses local login)",
        )
        table.add_row("Anthropic API key", "set" if cfg.anthropic_api_key else "(not set)")
        table.add_row("GitHub token", "set" if cfg.github_token else "(not set)")
        table.add_row("Fleet enabled", str(cfg.fleet_enabled))
        table.add_row("Fleet apply", str(cfg.fleet_apply))
        table.add_row("Max Recursion Depth", str(cfg.max_recursion_depth))
        table.add_row("Fitness Decay Rate", str(cfg.fitness_decay_rate))
        table.add_row("Auto Execute", str(cfg.auto_execute))
        table.add_row("Timeout (seconds)", str(cfg.timeout_seconds))
        table.add_row("Database Path", str(cfg.db_path))
        table.add_row("Theme", cfg.theme)

        console.print(table)
        return

    console.info("Configuration updated. (Note: Set via environment variables for persistence)")


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        "-h",
        help="Host to bind to (default: WTD_API_HOST or 127.0.0.1)",
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to bind to (default: WTD_API_PORT or 8787)",
    ),
):
    """
    🌐 Start the WTD API server.

    Binds to 127.0.0.1 by default. The REST API can trigger workspace
    actions, so do not expose it on a public interface without setting up
    authentication first.
    """
    import uvicorn

    from wtd.api.app import create_app

    cfg = get_config()
    bind_host = host or cfg.api_host
    bind_port = port if port is not None else cfg.api_port

    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        console.warning(
            f"Binding WTD API to {bind_host}: the API can trigger workspace "
            "actions and currently has no authentication. Make sure this is "
            "intentional and the network is trusted."
        )

    console.info(f"Starting WTD API server on http://{bind_host}:{bind_port}")
    console.print("[dim]Press Ctrl+C to stop[/]")

    api_app = create_app()
    uvicorn.run(api_app, host=bind_host, port=bind_port)


# ============================================================================
# Routines Subcommand Group
# ============================================================================

routines_app = typer.Typer(
    name="routines",
    help="🔄 Manage routine/recurring TODOs",
    rich_markup_mode="rich",
)
app.add_typer(routines_app, name="routines")


@routines_app.callback(invoke_without_command=True)
def routines_default(ctx: typer.Context):
    """Show routine summary if no subcommand given."""
    if ctx.invoked_subcommand is None:
        routines_list()


@routines_app.command("list")
def routines_list(
    show_archived: bool = typer.Option(
        False,
        "--archived",
        "-a",
        help="Include archived routines",
    ),
):
    """
    📋 List all routines.
    """
    from wtd.core.routines import RoutineManager
    from wtd.ui.routines_output import print_all_routines, print_routine_summary

    manager = RoutineManager()
    print_routine_summary(manager)
    print_all_routines(manager, show_archived)


@routines_app.command("due")
def routines_due():
    """
    ⏰ Show routines that are due now.
    """
    from wtd.core.routines import RoutineManager
    from wtd.ui.routines_output import print_due_routines, print_routine_summary

    manager = RoutineManager()
    print_routine_summary(manager)

    due = manager.get_due_routines()
    overdue = manager.get_overdue_routines()

    if overdue:
        console.print()
        console.print(f"[bold red]🚨 {len(overdue)} Overdue Routine(s)![/]")

    print_due_routines(due)


@routines_app.command("review")
def routines_review():
    """
    🔍 Review routines that need re-evaluation.
    """
    from wtd.core.routines import RoutineManager
    from wtd.ui.routines_output import print_routines_needing_review

    manager = RoutineManager()
    routines = manager.get_routines_needing_review()

    console.header("🔍 Routine Review")
    print_routines_needing_review(routines)


@routines_app.command("sync")
def routines_sync(
    path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan for routine files",
        exists=True,
        resolve_path=True,
    ),
):
    """
    🔄 Sync routines from markdown files.

    Scans for routines.md files and imports routine definitions.
    """
    from wtd.core.routines import RoutineManager
    from wtd.ui.routines_output import print_sync_result

    scan_path = path or Path.cwd()
    manager = RoutineManager()

    with console.spinner() as progress:
        task = progress.add_task("Syncing routines...", total=None)
        stats = manager.sync_from_files(scan_path)
        progress.remove_task(task)

    print_sync_result(stats)


@routines_app.command("complete")
def routines_complete(
    routine_id: str = typer.Argument(
        ...,
        help="Routine ID or partial title to complete",
    ),
    notes: str = typer.Option(
        "",
        "--notes",
        "-n",
        help="Completion notes",
    ),
):
    """
    ✅ Mark a routine as completed for this cycle.
    """
    from uuid import UUID

    from wtd.core.routines import RoutineManager

    manager = RoutineManager()

    # Try to find by ID or title
    routine = None
    try:
        routine = manager.get(UUID(routine_id))
    except ValueError:
        # Search by title
        for r in manager.routines.values():
            if routine_id.lower() in r.title.lower():
                routine = r
                break

    if routine is None:
        console.error(f"Routine not found: {routine_id}")
        raise typer.Exit(1)

    manager.complete_routine(routine.id, notes)
    console.success(f"Completed: {routine.title}")

    if routine.next_due:
        console.info(f"Next due: {routine.next_due.strftime('%Y-%m-%d %H:%M')}")


@routines_app.command("skip")
def routines_skip(
    routine_id: str = typer.Argument(
        ...,
        help="Routine ID or partial title to skip",
    ),
    reason: str = typer.Option(
        "",
        "--reason",
        "-r",
        help="Reason for skipping",
    ),
):
    """
    ⏭️  Skip a routine for this cycle.
    """
    from uuid import UUID

    from wtd.core.routines import RoutineManager

    manager = RoutineManager()

    # Try to find by ID or title
    routine = None
    try:
        routine = manager.get(UUID(routine_id))
    except ValueError:
        for r in manager.routines.values():
            if routine_id.lower() in r.title.lower():
                routine = r
                break

    if routine is None:
        console.error(f"Routine not found: {routine_id}")
        raise typer.Exit(1)

    manager.skip_routine(routine.id, reason)
    console.warning(f"Skipped: {routine.title}")
    console.muted("Note: Frequently skipping a routine will flag it for review")


@routines_app.command("snooze")
def routines_snooze(
    routine_id: str = typer.Argument(
        ...,
        help="Routine ID or partial title to snooze",
    ),
    hours: int = typer.Option(
        24,
        "--hours",
        "-h",
        help="Hours to snooze",
    ),
):
    """
    💤 Snooze a routine for a specified time.
    """
    from uuid import UUID

    from wtd.core.routines import RoutineManager

    manager = RoutineManager()

    routine = None
    try:
        routine = manager.get(UUID(routine_id))
    except ValueError:
        for r in manager.routines.values():
            if routine_id.lower() in r.title.lower():
                routine = r
                break

    if routine is None:
        console.error(f"Routine not found: {routine_id}")
        raise typer.Exit(1)

    manager.snooze_routine(routine.id, hours)
    console.info(f"Snoozed '{routine.title}' for {hours} hours")


@routines_app.command("pause")
def routines_pause(
    routine_id: str = typer.Argument(
        ...,
        help="Routine ID or partial title to pause",
    ),
):
    """
    ⏸️  Pause a routine indefinitely.
    """
    from uuid import UUID

    from wtd.core.routines import RoutineManager

    manager = RoutineManager()

    routine = None
    try:
        routine = manager.get(UUID(routine_id))
    except ValueError:
        for r in manager.routines.values():
            if routine_id.lower() in r.title.lower():
                routine = r
                break

    if routine is None:
        console.error(f"Routine not found: {routine_id}")
        raise typer.Exit(1)

    routine.pause()
    manager._save()
    console.warning(f"Paused: {routine.title}")
    console.muted("Use 'wtd routines resume' to resume")


@routines_app.command("resume")
def routines_resume(
    routine_id: str = typer.Argument(
        ...,
        help="Routine ID or partial title to resume",
    ),
):
    """
    ▶️  Resume a paused routine.
    """
    from uuid import UUID

    from wtd.core.routines import RoutineManager

    manager = RoutineManager()

    routine = None
    try:
        routine = manager.get(UUID(routine_id))
    except ValueError:
        for r in manager.routines.values():
            if routine_id.lower() in r.title.lower():
                routine = r
                break

    if routine is None:
        console.error(f"Routine not found: {routine_id}")
        raise typer.Exit(1)

    routine.resume()
    manager._save()
    console.success(f"Resumed: {routine.title}")

    if routine.next_due:
        console.info(f"Next due: {routine.next_due.strftime('%Y-%m-%d %H:%M')}")


@routines_app.command("trigger")
def routines_trigger(
    trigger_name: str = typer.Argument(
        ...,
        help="Trigger name (on-release, on-pr, on-deploy, etc.)",
    ),
):
    """
    ⚡ Trigger conditional routines.

    Use this to manually trigger event-based routines like on-release or on-deploy.
    """
    from wtd.core.routines import RoutineManager, RoutineTrigger
    from wtd.ui.routines_output import print_due_routines

    # Map trigger name
    trigger_map = {
        "on-release": RoutineTrigger.ON_RELEASE,
        "on-pr": RoutineTrigger.ON_PR,
        "on-deploy": RoutineTrigger.ON_DEPLOY,
        "on-error-spike": RoutineTrigger.ON_ERROR_SPIKE,
        "on-commit": RoutineTrigger.ON_COMMIT,
        "on-merge": RoutineTrigger.ON_MERGE,
        "on-startup": RoutineTrigger.ON_STARTUP,
    }

    trigger = trigger_map.get(trigger_name.lower())
    if trigger is None:
        console.error(f"Unknown trigger: {trigger_name}")
        console.info(f"Valid triggers: {', '.join(trigger_map.keys())}")
        raise typer.Exit(1)

    manager = RoutineManager()
    triggered = manager.trigger_conditional(trigger)

    if triggered:
        console.success(f"Triggered {len(triggered)} routine(s) for '{trigger_name}'")
        print_due_routines(triggered)
    else:
        console.info(f"No routines configured for trigger: {trigger_name}")


# ============================================================================
# Fleet Subcommand Group — the autonomous AI agent fleet platform
# ============================================================================

fleet_app = typer.Typer(
    name="fleet",
    help="🚁 Orchestrate, monitor, and load-balance the AI agent fleet",
    rich_markup_mode="rich",
)
app.add_typer(fleet_app, name="fleet")


def _with_orchestrator(work):
    """Run an async orchestrator task with clean client shutdown."""
    from wtd.fleet.orchestrator import FleetOrchestrator

    async def _run():
        orchestrator = FleetOrchestrator()
        try:
            return await work(orchestrator)
        finally:
            await orchestrator.aclose()

    return asyncio.run(_run())


@fleet_app.callback(invoke_without_command=True)
def fleet_default(ctx: typer.Context):
    """Show fleet status when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        fleet_status_cmd()


@fleet_app.command("status")
def fleet_status_cmd():
    """📡 Show fleet health: providers, roster, queue, budgets, recent runs."""
    from wtd.fleet.monitor import fleet_status
    from wtd.ui.fleet_output import print_fleet_status

    print_fleet_status(fleet_status())


@fleet_app.command("agents")
def fleet_agents():
    """🤖 List agent roles (built-ins + agents/*.md overrides)."""
    from wtd.fleet.roles import load_roles
    from wtd.fleet.settings import load_settings
    from wtd.ui.fleet_output import print_roles

    settings = load_settings()
    print_roles(load_roles(enabled=settings.roles_enabled or None))


@fleet_app.command("queue")
def fleet_queue(
    all_items: bool = typer.Option(
        False, "--all", "-a", help="Include done/failed/skipped items"
    ),
):
    """📋 Show the cross-repo work queue."""
    from wtd.fleet.models import WorkStatus
    from wtd.fleet.state import FleetState
    from wtd.ui.fleet_output import print_queue

    cfg = get_config()
    state = FleetState(cfg.fleet_state_path).load()
    items = list(state.items.values())
    if not all_items:
        items = [
            i
            for i in items
            if i.status in (WorkStatus.QUEUED, WorkStatus.DEFERRED, WorkStatus.SCHEDULED)
        ]
    items.sort(key=lambda i: (i.status.value, i.repo, i.created_at))
    print_queue(items)


@fleet_app.command("discover")
def fleet_discover(
    repo: list[str] = typer.Option(
        None, "--repo", "-r", help="Limit to specific owner/repo (repeatable)"
    ),
):
    """🔍 Scan the roster for new work and enqueue it (no agents run)."""

    async def _work(orchestrator):
        new = await orchestrator.discover(repo or None)
        console.success(f"Discovery complete: {new} new work item(s) enqueued.")
        pending = orchestrator.state.pending(
            max_attempts=orchestrator.settings.max_attempts
        )
        console.info(f"Queue now has {len(pending)} pending item(s).")

    _with_orchestrator(_work)


@fleet_app.command("plan")
def fleet_plan(
    repo: list[str] = typer.Option(None, "--repo", "-r", help="Filter repos"),
    role: list[str] = typer.Option(None, "--role", help="Filter roles"),
    max_runs: Optional[int] = typer.Option(None, "--max-runs", "-n"),
):
    """🗺️  Show what the next cycle would run (no agents, no writes)."""
    from wtd.ui.fleet_output import print_plan

    async def _work(orchestrator):
        plan = orchestrator.plan(
            repos=repo or None, roles=role or None, max_runs=max_runs
        )
        print_plan(plan)

    _with_orchestrator(_work)


@fleet_app.command("run")
def fleet_run(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually write to GitHub (comments, labels, issues, PRs). "
        "Default is dry-run.",
    ),
    repo: list[str] = typer.Option(None, "--repo", "-r", help="Filter repos"),
    role: list[str] = typer.Option(None, "--role", help="Filter roles"),
    max_runs: Optional[int] = typer.Option(None, "--max-runs", "-n"),
    skip_discovery: bool = typer.Option(
        False, "--skip-discovery", help="Work the existing queue only"
    ),
):
    """▶️  Run ONE fleet cycle: discover → schedule → dispatch agents."""
    from wtd.ui.fleet_output import print_cycle_report

    async def _work(orchestrator):
        report = await orchestrator.cycle(
            apply=apply or None,
            repos=repo or None,
            roles=role or None,
            max_runs=max_runs,
            skip_discovery=skip_discovery,
        )
        print_cycle_report(report)

    _with_orchestrator(_work)


@fleet_app.command("loop")
def fleet_loop(
    apply: bool = typer.Option(False, "--apply", help="Write mode (default dry-run)"),
    interval: Optional[int] = typer.Option(
        None, "--interval", "-i", help="Seconds between cycles (default from config)"
    ),
    cycles: Optional[int] = typer.Option(
        None, "--cycles", "-c", help="Stop after N cycles (default: forever)"
    ),
):
    """🔁 Run the autonomous loop: cycle, sleep, repeat. Ctrl+C to stop."""
    from wtd.ui.fleet_output import print_cycle_report

    async def _work(orchestrator):
        console.info(
            f"Fleet loop starting (interval "
            f"{interval or orchestrator.config.fleet_interval_seconds}s, "
            f"{'APPLY' if apply else 'dry-run'} mode). Ctrl+C to stop."
        )
        await orchestrator.loop(
            apply=apply or None,
            interval_seconds=interval,
            max_cycles=cycles,
            on_report=print_cycle_report,
        )

    try:
        _with_orchestrator(_work)
    except KeyboardInterrupt:
        console.info("Fleet loop stopped.")


@fleet_app.command("daily")
def fleet_daily(
    apply: bool = typer.Option(
        False, "--apply", help="Write to GitHub (reviews, docs PRs, merges)."
    ),
    repo: list[str] = typer.Option(None, "--repo", "-r", help="Filter repos"),
    max_runs: Optional[int] = typer.Option(None, "--max-runs", "-n"),
    no_agents: bool = typer.Option(
        False, "--no-agents", help="Sweep and report only; run no agents."
    ),
    discover: bool = typer.Option(
        False, "--discover", help="Also run the per-cycle scanners (issues, CI)."
    ),
):
    """📅 The daily pass: docs drift → review every PR → merge what is green."""
    from wtd.ui.fleet_output import print_cycle_report, print_daily_report

    async def _work(orchestrator):
        report, cycle = await orchestrator.daily(
            apply=apply or None,
            repos=repo or None,
            max_runs=max_runs,
            run_agents=not no_agents,
            discover=discover,
        )
        print_daily_report(report)
        if cycle is not None:
            print_cycle_report(cycle)

    _with_orchestrator(_work)


@fleet_app.command("merge-check")
def fleet_merge_check(
    repo: list[str] = typer.Option(None, "--repo", "-r", help="Filter repos"),
    require_approval: bool = typer.Option(
        False,
        "--require-approval",
        help="Also require a standing fleet review approval (as the sweep does).",
    ),
):
    """🔍 Read-only: why each open PR would, or would not, be merged."""
    from wtd.ui.fleet_output import print_merge_check

    async def _work(orchestrator):
        attempts = await orchestrator.harness().inspect_merges(
            repo or None, require_approval=require_approval or False
        )
        print_merge_check(attempts, require_approval=require_approval)

    _with_orchestrator(_work)


@fleet_app.command("budget")
def fleet_budget():
    """💰 Show today's token budgets and burn per provider lane."""
    from dataclasses import asdict

    from wtd.fleet.balancer import CapacityBalancer, default_lanes
    from wtd.fleet.settings import load_settings
    from wtd.ui.fleet_output import print_lanes

    cfg = get_config()
    settings = load_settings(cfg)
    balancer = CapacityBalancer(
        default_lanes(settings), cfg.fleet_state_path / "capacity.json"
    )
    print_lanes([asdict(s) for s in balancer.snapshot()])


def _collect_manifests(repos, paths, use_github):
    """Gather manifests for the requested repos, from disk or GitHub."""
    import asyncio
    from pathlib import Path as _Path

    from wtd.fleet.adopt import derive_manifest, derive_manifest_from_github
    from wtd.fleet.manifest import MANIFEST_FILENAME, ManifestError, load_manifest
    from wtd.fleet.settings import load_settings

    cfg = get_config()
    slugs = list(repos) if repos else load_settings(cfg).repo_slugs()
    if not slugs and not paths:
        console.error("No repos to inspect. Set fleet.repos in wtd.yml, or pass --repo/--path.")
        raise typer.Exit(1)

    manifests = []
    # Local checkouts first: a committed manifest always wins over inference.
    for raw in paths or []:
        root = _Path(raw).expanduser().resolve()
        if not root.is_dir():
            console.warning(f"skipping {root}: not a directory")
            continue
        committed = root / MANIFEST_FILENAME
        slug = f"local/{root.name}"
        if committed.is_file():
            try:
                manifests.append(load_manifest(committed))
                continue
            except ManifestError as exc:
                console.warning(f"{committed}: {exc}; falling back to inference")
        manifests.append(derive_manifest(root, slug))

    if slugs and use_github:
        async def _fetch():
            from wtd.fleet.github import GitHubClient

            client = GitHubClient(cfg.github_token, api_url=cfg.github_api_url)
            try:
                return [await derive_manifest_from_github(client, s) for s in slugs]
            finally:
                await client.aclose()

        manifests.extend(asyncio.run(_fetch()))
    return manifests


@fleet_app.command("map")
def fleet_map(
    repo: list[str] = typer.Option(None, "--repo", "-r", help="owner/name (repeatable)"),
    path: list[str] = typer.Option(
        None, "--path", "-P", help="Local checkout to inspect (repeatable)"
    ),
    no_github: bool = typer.Option(
        False, "--no-github", help="Only inspect --path checkouts"
    ),
):
    """🗺  Show every AI lane across the fleet in one view."""
    from wtd.ui.harmony_output import print_fleet_map

    print_fleet_map(_collect_manifests(repo, path, not no_github))


@fleet_app.command("audit")
def fleet_audit(
    repo: list[str] = typer.Option(None, "--repo", "-r", help="owner/name (repeatable)"),
    path: list[str] = typer.Option(None, "--path", "-P", help="Local checkout (repeatable)"),
    no_github: bool = typer.Option(False, "--no-github", help="Only inspect --path checkouts"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include info findings"),
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero when any critical finding is found"
    ),
):
    """⚖  Audit every repo against the fleet's shared conventions."""
    from wtd.fleet.conventions import audit_fleet
    from wtd.ui.harmony_output import print_audit

    reports = audit_fleet(_collect_manifests(repo, path, not no_github))
    print_audit(reports, verbose=verbose)
    if strict and any(r.by_severity("critical") for r in reports):
        raise typer.Exit(1)


@fleet_app.command("adopt")
def fleet_adopt(
    path: str = typer.Argument(..., help="Local repo checkout to read"),
    repo: Optional[str] = typer.Option(
        None, "--repo", "-r", help="owner/name to stamp (default: inferred from git remote)"
    ),
    write: bool = typer.Option(
        False, "--write", help="Write fleet.manifest.yml into the repo"
    ),
):
    """🧬 Derive a repo's fleet manifest from its workflows."""
    import subprocess

    from wtd.fleet.adopt import derive_manifest
    from wtd.fleet.manifest import MANIFEST_FILENAME
    from wtd.ui.harmony_output import print_manifest

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        console.error(f"Not a directory: {root}")
        raise typer.Exit(1)

    slug = repo
    if not slug:
        try:
            url = subprocess.run(
                ["git", "-C", str(root), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip()
            match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
            slug = match.group(1) if match else f"local/{root.name}"
        except (OSError, subprocess.SubprocessError):
            slug = f"local/{root.name}"

    manifest = derive_manifest(root, slug)
    if write:
        target = root / MANIFEST_FILENAME
        target.write_text(manifest.to_yaml(), encoding="utf-8")
        console.success(f"Wrote {target}")
        console.muted(
            f"{len(manifest.lanes)} lane(s) detected — review it: inference is a "
            "starting point, not the last word."
        )
    else:
        print_manifest(manifest)


@fleet_app.command("init")
def fleet_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
):
    """🧰 Write a starter wtd.yml and agents/ directory here."""
    wtd_yml = Path.cwd() / "wtd.yml"
    agents_dir = Path.cwd() / "agents"

    if wtd_yml.exists() and not force:
        console.warning(f"{wtd_yml} already exists (use --force to overwrite).")
    else:
        wtd_yml.write_text(STARTER_WTD_YML, encoding="utf-8")
        console.success(f"Wrote {wtd_yml}")

    agents_dir.mkdir(exist_ok=True)
    readme = agents_dir / "README.md"
    if not readme.exists() or force:
        readme.write_text(STARTER_AGENTS_README, encoding="utf-8")
        console.success(f"Wrote {readme}")

    console.info("Next steps:")
    console.print("  1. Edit wtd.yml — add your repos to fleet.repos")
    console.print("  2. Export a GitHub token (GITHUB_TOKEN) for discovery")
    console.print("  3. wtd fleet status   # check provider lanes")
    console.print("  4. wtd fleet run      # first dry-run cycle")
    console.print("  5. wtd fleet run --apply   # let the agents act")


STARTER_WTD_YML = """\
# wtd.yml — fleet configuration (see docs/FLEET.md)
fleet:
  repos:
    # - repo: your-user/your-repo
    #   roles: [triage, reviewer, doc-writer]   # optional; default: all enabled
    #   articles: false                          # opt-in weekly article drafts
  # roles_enabled: [triage, bug-hunter, reviewer, janitor, doc-writer, contributor, author]
  scan:
    issues: true
    pulls: true
    ci: true
    docs: true
  max_runs_per_cycle: 8
  max_writes_per_cycle: 5
  budgets:
    claude_code_daily_tokens: 1500000
    anthropic_daily_tokens: 500000
    anthropic_daily_usd: 10
"""

STARTER_AGENTS_README = """\
# agents/

Custom agent role definitions. Each `<name>.md` file overrides or extends
the built-in fleet roles (`wtd fleet agents` lists them).

Format: YAML frontmatter + the system prompt as the body.

```markdown
---
name: security-auditor
description: Reviews changes for security issues.
kinds: [review_pr]
actions: [comment]
max_tokens: 6000
---
You are a security-focused reviewer. Given a pull request diff, look for
injection risks, secret leakage, unsafe deserialization, and permission
widening. Comment only when you find something concrete.
```

Fields: `name`, `description`, `kinds` (work kinds handled), `actions`
(comment | add_labels | create_issue | propose_pr), `model`,
`max_tokens`, `est_tokens`.
"""


if __name__ == "__main__":
    app()
