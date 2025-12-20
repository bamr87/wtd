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
from pathlib import Path
from typing import Optional

import typer
from rich.prompt import Confirm, Prompt

from wtd import __version__
from wtd.config import WTDConfig, get_config
from wtd.core.agent import WTDAgent
from wtd.core.scanner import TodoScanner
from wtd.core.tree import TodoTree
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
        help="LLM provider (ollama, openai, anthropic)",
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
    
    asyncio.run(_main_flow(
        scan_path,
        interactive=interactive,
        auto_execute=auto_execute,
        provider=provider,
    ))


async def _main_flow(
    path: Path,
    interactive: bool = True,
    auto_execute: bool = False,
    provider: str | None = None,
):
    """Main WTD flow."""
    config = get_config()
    
    if provider:
        config.llm_provider = provider
    
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

    # Step 3: Initialize TODO tree
    tree = agent.initialize_tree(scan_result)
    print_todo_tree(tree)

    # Step 4: Suggest and set up workspace
    workspace_config = await agent.suggest_workspace(context, scan_result.todos)
    
    if workspace_config.files_to_open or workspace_config.terminals or workspace_config.browser_urls:
        if Confirm.ask("Set up workspace with suggested configuration?", default=True):
            orchestrator = WorkspaceOrchestrator(path)
            
            with console.spinner() as progress:
                task = progress.add_task("Setting up workspace...", total=None)
                results = await orchestrator.setup_workspace(workspace_config)
                progress.remove_task(task)
            
            print_workspace_setup(results)

    # Step 5: Run interactive dashboard or execute
    if interactive:
        console.info("Launching interactive dashboard...")
        console.print()
        console.print("[dim]Press 'q' to quit, '?' for help[/]")
        
        from wtd.ui.dashboard import run_dashboard
        run_dashboard(tree)
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
        
        # Initialize tree
        tree = TodoTree()
        tree.add_todos(scan_result.todos)
        
        # Run dashboard
        from wtd.ui.dashboard import run_dashboard
        run_dashboard(tree)

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
        
        # Initialize
        agent = WTDAgent(config)
        tree = agent.initialize_tree(scan_result)
        
        # Get next actionable
        next_todo = tree.get_next_actionable()
        
        if not next_todo:
            console.success("No actionable TODOs!")
            return
        
        console.info(f"Next TODO: {next_todo.title}")
        
        if auto or Confirm.ask("Execute?", default=True):
            result = await agent.execute_todo(next_todo)
            print_execution_result(next_todo, result)

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
        
        tree = TodoTree()
        tree.add_todos(scan_result.todos)
        
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
        table.add_row("Ollama Model", cfg.ollama_model)
        table.add_row("Ollama Host", cfg.ollama_host)
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
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        "-h",
        help="Host to bind to",
    ),
    port: int = typer.Option(
        8787,
        "--port",
        "-p",
        help="Port to bind to",
    ),
):
    """
    🌐 Start the WTD API server.
    """
    import uvicorn
    from wtd.api.app import create_app
    
    console.info(f"Starting WTD API server on http://{host}:{port}")
    console.print("[dim]Press Ctrl+C to stop[/]")
    
    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    app()

