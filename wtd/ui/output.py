"""
WTD Rich Output - Beautiful terminal output
"""

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.text import Text
from rich.tree import Tree as RichTree
from rich.style import Style
from rich.theme import Theme

from wtd.core.models import (
    ScanResult,
    TodoContext,
    TodoNode,
    TodoPriority,
    TodoStatus,
)
from wtd.core.tree import TodoTree


# WTD custom theme - vibrant and distinctive
WTD_THEME = Theme({
    "wtd.header": "bold magenta",
    "wtd.success": "bold green",
    "wtd.error": "bold red",
    "wtd.warning": "bold yellow",
    "wtd.info": "bold cyan",
    "wtd.muted": "dim white",
    "wtd.highlight": "bold bright_white on dark_magenta",
    
    # Status colors
    "status.pending": "yellow",
    "status.in_progress": "cyan",
    "status.completed": "green",
    "status.blocked": "red",
    "status.cancelled": "dim red",
    "status.collapsed": "dim white",
    
    # Priority colors
    "priority.critical": "bold red",
    "priority.high": "bold orange1",
    "priority.medium": "yellow",
    "priority.low": "dim cyan",
    
    # Context colors
    "context.bugfix": "red",
    "context.write": "blue",
    "context.plan": "magenta",
    "context.learn": "green",
    "context.build": "cyan",
    "context.refactor": "yellow",
    "context.test": "orange1",
    "context.deploy": "purple",
    "context.unknown": "white",
})


# Status icons
STATUS_ICONS = {
    TodoStatus.PENDING: "○",
    TodoStatus.IN_PROGRESS: "◐",
    TodoStatus.COMPLETED: "●",
    TodoStatus.BLOCKED: "✗",
    TodoStatus.CANCELLED: "⊘",
    TodoStatus.COLLAPSED: "◌",
}

# Context icons
CONTEXT_ICONS = {
    TodoContext.BUGFIX: "🐛",
    TodoContext.WRITE: "✍️",
    TodoContext.PLAN: "📋",
    TodoContext.LEARN: "📚",
    TodoContext.BUILD: "🔨",
    TodoContext.REFACTOR: "🔧",
    TodoContext.TEST: "🧪",
    TodoContext.DEPLOY: "🚀",
    TodoContext.UNKNOWN: "❓",
}

# Priority icons
PRIORITY_ICONS = {
    TodoPriority.CRITICAL: "🔴",
    TodoPriority.HIGH: "🟠",
    TodoPriority.MEDIUM: "🟡",
    TodoPriority.LOW: "🟢",
}


class Console:
    """WTD Console wrapper with custom theming."""

    def __init__(self):
        self.console = RichConsole(theme=WTD_THEME)

    def print(self, *args, **kwargs):
        """Print to console."""
        self.console.print(*args, **kwargs)

    def header(self, text: str):
        """Print a styled header."""
        self.console.print()
        self.console.print(
            Panel(
                Text(text, justify="center", style="wtd.header"),
                border_style="magenta",
                padding=(1, 2),
            )
        )
        self.console.print()

    def success(self, text: str):
        """Print success message."""
        self.console.print(f"[wtd.success]✓[/] {text}")

    def error(self, text: str):
        """Print error message."""
        self.console.print(f"[wtd.error]✗[/] {text}")

    def warning(self, text: str):
        """Print warning message."""
        self.console.print(f"[wtd.warning]![/] {text}")

    def info(self, text: str):
        """Print info message."""
        self.console.print(f"[wtd.info]ℹ[/] {text}")

    def muted(self, text: str):
        """Print muted/secondary text."""
        self.console.print(f"[wtd.muted]{text}[/]")

    def rule(self, title: str = ""):
        """Print a horizontal rule."""
        self.console.rule(title, style="magenta")

    def spinner(self, message: str = "Working..."):
        """Create a spinner context."""
        return Progress(
            SpinnerColumn(spinner_name="dots"),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=True,
        )


# Global console instance
console = Console()


def print_scan_result(result: ScanResult):
    """Print scan results in a beautiful format."""
    console.header("🔍 WTD Scan Results")

    # Summary table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="wtd.muted")
    table.add_column("Value", style="wtd.info")

    table.add_row("TODOs Found", str(len(result.todos)))
    table.add_row("Context", f"{CONTEXT_ICONS.get(result.context, '')} {result.context.value}")
    table.add_row("Confidence", f"{result.confidence:.0%}")
    table.add_row("Scan Time", f"{result.scan_duration_ms}ms")
    table.add_row("Sources Scanned", str(result.sources_scanned))

    console.print(Panel(table, title="Summary", border_style="cyan"))

    # Display found tasks
    if result.todos:
        console.print()
        console.print("[bold]TODOs:[/]")
        
        todo_table = Table(show_header=True, header_style="bold magenta", expand=True)
        todo_table.add_column("#", width=3, justify="right")
        todo_table.add_column("Pri", width=6)
        todo_table.add_column("Context", width=8)
        todo_table.add_column("Title", ratio=3)
        todo_table.add_column("Source", ratio=1)

        for i, todo in enumerate(result.todos[:20], 1):
            priority_style = f"priority.{todo.priority.value}"
            context_style = f"context.{todo.context.value}"
            
            source_str = ""
            if todo.source and todo.source.file_path:
                source_str = f"{todo.source.file_path.name}:{todo.source.line_number or '?'}"

            todo_table.add_row(
                str(i),
                Text(todo.priority.value, style=priority_style),
                Text(todo.context.value, style=context_style),
                todo.title[:47] + "..." if len(todo.title) > 50 else todo.title,
                source_str,
            )

        console.print(todo_table)

        if len(result.todos) > 20:
            console.muted(f"  ... and {len(result.todos) - 20} more TODOs")
    else:
        console.warning("No TODOs found in this directory.")


def print_todo_tree(tree: TodoTree):
    """Print the TODO tree in a beautiful format."""
    console.header("🌳 TODO Tree")

    # Progress stats
    progress = tree.get_progress()
    
    progress_table = Table(show_header=False, box=None, padding=(0, 1))
    progress_table.add_column("Status", width=15)
    progress_table.add_column("Count", width=5, justify="right")
    progress_table.add_column("Bar", width=20)

    total = progress["total"] or 1
    
    for status, count in [
        ("Completed", progress["completed"]),
        ("In Progress", progress["in_progress"]),
        ("Pending", progress["pending"]),
        ("Blocked", progress["blocked"]),
    ]:
        bar_width = int((count / total) * 20)
        bar = "█" * bar_width + "░" * (20 - bar_width)
        progress_table.add_row(status, str(count), bar)

    console.print(Panel(
        progress_table,
        title=f"Progress: {progress['percentage']:.1f}%",
        border_style="green" if progress["percentage"] == 100 else "cyan"
    ))

    # Tree visualization
    console.print()
    
    for root in tree.root_nodes:
        rich_tree = _build_rich_tree(root, tree)
        console.print(rich_tree)
        console.print()


def _build_rich_tree(node: TodoNode, tree: TodoTree) -> RichTree:
    """Build a Rich tree from a TodoNode."""
    status_icon = STATUS_ICONS.get(node.status, "?")
    context_icon = CONTEXT_ICONS.get(node.context, "")
    priority_icon = PRIORITY_ICONS.get(node.priority, "")
    
    status_style = f"status.{node.status.value}"
    
    label = Text()
    label.append(status_icon + " ", style=status_style)
    label.append(priority_icon + " ")
    label.append(node.title, style="bold" if node.status == TodoStatus.IN_PROGRESS else None)
    label.append(f" {context_icon}", style=f"context.{node.context.value}")
    
    if node.depth > 0:
        label.append(f" [d{node.depth}]", style="dim")

    rich_tree = RichTree(label)

    # Add children
    children = tree.get_children(node.id)
    for child in children:
        child_tree = _build_rich_tree(child, tree)
        rich_tree.add(child_tree)

    return rich_tree


def print_workspace_setup(results: dict):
    """Print workspace setup results."""
    console.header("🖥️  Workspace Setup")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Component", width=15)
    table.add_column("Status", width=10)
    table.add_column("Details", width=40)

    # VSCode
    if results.get("vscode"):
        table.add_row("VSCode", "[green]✓[/]", "Opened workspace")
    
    # Files
    for f in results.get("files", []):
        table.add_row("File", "[green]✓[/]", str(f))
    
    # Terminals
    for t in results.get("terminals", []):
        table.add_row("Terminal", "[green]✓[/]", t)
    
    # Browser
    for url in results.get("browser", []):
        table.add_row("Browser", "[green]✓[/]", url)
    
    # Errors
    for err in results.get("errors", []):
        table.add_row("Error", "[red]✗[/]", err)

    console.print(table)


def print_execution_result(todo: TodoNode, result):
    """Print execution result."""
    console.print()
    
    if result.success:
        console.success(f"Executed: {todo.title}")
    else:
        console.error(f"Failed: {todo.title}")
        if result.error:
            console.muted(f"  Error: {result.error}")

    if result.output:
        console.info(result.output)

    if result.spawned_todos:
        console.print(f"  [cyan]Spawned {len(result.spawned_todos)} subtasks:[/]")
        for st in result.spawned_todos:
            console.print(f"    {STATUS_ICONS[st.status]} {st.title}")


def print_banner():
    """Print the WTD banner."""
    banner = """
╦ ╦╔╦╗╔╦╗
║║║ ║  ║║
╚╩╝ ╩ ═╩╝
    """
    console.console.print(
        Panel(
            Text(banner + "\nWhat To Do - The Ultimate Recursive TODO Engine", 
                 justify="center", style="bold magenta"),
            border_style="magenta",
            padding=(0, 4),
        )
    )

