"""
WTD Dashboard - Interactive terminal dashboard using Textual
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from wtd.core.models import TodoContext, TodoNode, TodoPriority, TodoStatus
from wtd.core.tree import TodoTree
from wtd.core.tree_store import TreeStore


# Status styles
STATUS_COLORS = {
    TodoStatus.PENDING: "yellow",
    TodoStatus.IN_PROGRESS: "cyan",
    TodoStatus.COMPLETED: "green",
    TodoStatus.BLOCKED: "red",
    TodoStatus.CANCELLED: "dim",
    TodoStatus.COLLAPSED: "dim",
}

STATUS_ICONS = {
    TodoStatus.PENDING: "○",
    TodoStatus.IN_PROGRESS: "◐",
    TodoStatus.COMPLETED: "●",
    TodoStatus.BLOCKED: "✗",
    TodoStatus.CANCELLED: "⊘",
    TodoStatus.COLLAPSED: "◌",
}

PRIORITY_ICONS = {
    TodoPriority.CRITICAL: "🔴",
    TodoPriority.HIGH: "🟠",
    TodoPriority.MEDIUM: "🟡",
    TodoPriority.LOW: "🟢",
}


class ProgressPanel(Static):
    """Progress overview panel."""

    def __init__(self, tree: TodoTree, **kwargs):
        super().__init__(**kwargs)
        self.todo_tree = tree

    def compose(self) -> ComposeResult:
        progress = self.todo_tree.get_progress()
        
        yield Static(f"[bold magenta]Progress[/]", classes="panel-title")
        yield ProgressBar(
            total=max(progress["total"], 1),
            show_eta=False,
            show_percentage=True,
        )
        yield Static(
            f"✓ {progress['completed']} | "
            f"◐ {progress['in_progress']} | "
            f"○ {progress['pending']} | "
            f"✗ {progress['blocked']}"
        )

    def update_progress(self, tree: TodoTree):
        """Update progress display."""
        self.todo_tree = tree
        progress = tree.get_progress()
        
        progress_bar = self.query_one(ProgressBar)
        progress_bar.total = max(progress["total"], 1)
        progress_bar.progress = (
            progress["completed"] + progress["cancelled"] + progress["collapsed"]
        )


class TodoTreeWidget(Tree):
    """Interactive TODO tree widget."""

    def __init__(self, tree: TodoTree, **kwargs):
        super().__init__("TODOs", **kwargs)
        self.todo_tree = tree
        self.node_map: dict[str, UUID] = {}

    def on_mount(self) -> None:
        """Build the tree when mounted."""
        self.rebuild_tree()

    def rebuild_tree(self) -> None:
        """Rebuild the tree display."""
        self.clear()
        self.node_map.clear()
        
        for root in self.todo_tree.root_nodes:
            self._add_node(self.root, root)
        
        self.root.expand_all()

    def _add_node(self, parent: TreeNode, todo: TodoNode) -> TreeNode:
        """Add a TodoNode to the tree."""
        icon = STATUS_ICONS.get(todo.status, "?")
        priority = PRIORITY_ICONS.get(todo.priority, "")
        color = STATUS_COLORS.get(todo.status, "white")
        
        label = f"[{color}]{icon}[/] {priority} {todo.title[:40]}"
        if len(todo.title) > 40:
            label += "..."
        
        node = parent.add(label, data=todo.id)
        self.node_map[str(node.id)] = todo.id
        
        # Add children
        children = self.todo_tree.get_children(todo.id)
        for child in children:
            self._add_node(node, child)
        
        return node


class TodoDetailPanel(Static):
    """Panel showing selected TODO details."""

    selected_todo: reactive[TodoNode | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("[bold magenta]Details[/]", classes="panel-title")
        yield Static("Select a TODO to see details", id="detail-content")

    def watch_selected_todo(self, todo: TodoNode | None) -> None:
        """Update when selected TODO changes."""
        content = self.query_one("#detail-content", Static)
        
        if todo is None:
            content.update("Select a TODO to see details")
            return
        
        icon = STATUS_ICONS.get(todo.status, "?")
        priority = PRIORITY_ICONS.get(todo.priority, "")
        color = STATUS_COLORS.get(todo.status, "white")
        
        text = f"""[bold]{todo.title}[/]

[{color}]Status: {icon} {todo.status.value}[/]
Priority: {priority} {todo.priority.value}
Context: {todo.context.value}
Depth: {todo.depth}
Fitness: {todo.fitness_score:.2f}

{todo.description or 'No description'}
"""
        
        if todo.source and todo.source.file_path:
            text += f"\n[dim]Source: {todo.source.file_path}:{todo.source.line_number}[/]"
        
        if todo.actions:
            text += f"\n\n[bold]Planned Actions:[/]\n"
            for action in todo.actions[:5]:
                text += f"  • {action.get('action', 'unknown')}: {action.get('target', '')}\n"
        
        content.update(text)


class ActionPanel(Static):
    """Panel with action buttons."""

    def compose(self) -> ComposeResult:
        yield Static("[bold magenta]Actions[/]", classes="panel-title")
        with Horizontal(classes="button-row"):
            yield Button("▶ Execute", id="btn-execute", variant="primary")
            yield Button("↻ Spawn", id="btn-spawn", variant="default")
        with Horizontal(classes="button-row"):
            yield Button("✓ Complete", id="btn-complete", variant="success")
            yield Button("✗ Cancel", id="btn-cancel", variant="error")


class Dashboard(App):
    """WTD Interactive Dashboard."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 3 2;
        grid-gutter: 1;
        padding: 1;
        background: $surface;
    }

    .panel-title {
        text-style: bold;
        color: $primary;
        padding-bottom: 1;
    }

    .button-row {
        height: auto;
        margin-bottom: 1;
    }

    Button {
        margin-right: 1;
    }

    #tree-panel {
        column-span: 2;
        row-span: 2;
        border: solid $primary;
        padding: 1;
    }

    #progress-panel {
        border: solid $secondary;
        padding: 1;
    }

    #detail-panel {
        border: solid $secondary;
        padding: 1;
    }

    #action-panel {
        border: solid $accent;
        padding: 1;
    }

    Tree {
        background: $surface;
    }

    ProgressBar {
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "execute", "Execute"),
        Binding("c", "complete", "Complete"),
        Binding("s", "spawn", "Spawn Subtasks"),
        Binding("?", "help", "Help"),
    ]

    def __init__(self, tree: TodoTree | None = None, store: TreeStore | None = None, **kwargs):
        super().__init__(**kwargs)
        self.todo_tree = tree or TodoTree()
        self.selected_node_id: UUID | None = None
        self.store = store

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with Container(id="tree-panel"):
            yield TodoTreeWidget(self.todo_tree, id="todo-tree")
        
        with Container(id="progress-panel"):
            yield ProgressPanel(self.todo_tree)
        
        with Container(id="detail-panel"):
            yield TodoDetailPanel()
        
        with Container(id="action-panel"):
            yield ActionPanel()
        
        yield Footer()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle tree node selection."""
        tree_widget = self.query_one("#todo-tree", TodoTreeWidget)
        node_id = tree_widget.node_map.get(str(event.node.id))
        
        if node_id:
            self.selected_node_id = node_id
            todo = self.todo_tree.get_node(node_id)
            detail_panel = self.query_one(TodoDetailPanel)
            detail_panel.selected_todo = todo

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id
        
        if button_id == "btn-execute":
            self.action_execute()
        elif button_id == "btn-spawn":
            self.action_spawn()
        elif button_id == "btn-complete":
            self.action_complete()
        elif button_id == "btn-cancel":
            self.action_cancel()

    def action_refresh(self) -> None:
        """Refresh the display."""
        tree_widget = self.query_one("#todo-tree", TodoTreeWidget)
        tree_widget.rebuild_tree()
        
        progress_panel = self.query_one(ProgressPanel)
        progress_panel.update_progress(self.todo_tree)

    def action_execute(self) -> None:
        """Execute selected TODO."""
        if self.selected_node_id:
            self.todo_tree.start_node(self.selected_node_id)
            if self.store:
                self.store.apply_tree(self.todo_tree, source="dashboard_execute")
                self.store.save()
            self.action_refresh()
            self.notify("Started TODO execution", severity="information")

    def action_complete(self) -> None:
        """Mark selected TODO as complete."""
        if self.selected_node_id:
            self.todo_tree.complete_node(self.selected_node_id)
            if self.store:
                self.store.apply_tree(self.todo_tree, source="dashboard_complete")
                self.store.save()
            self.action_refresh()
            self.notify("TODO completed!", severity="information")

    def action_spawn(self) -> None:
        """Spawn subtasks for selected TODO."""
        if self.selected_node_id:
            # In real implementation, this would call the AI agent
            self.notify("Spawning subtasks...", severity="information")

    def action_cancel(self) -> None:
        """Cancel selected TODO."""
        if self.selected_node_id:
            self.todo_tree.cancel_node(self.selected_node_id)
            if self.store:
                self.store.apply_tree(self.todo_tree, source="dashboard_cancel")
                self.store.save()
            self.action_refresh()
            self.notify("TODO cancelled", severity="warning")

    def action_help(self) -> None:
        """Show help."""
        self.notify(
            "Keys: q=quit, r=refresh, e=execute, c=complete, s=spawn",
            severity="information",
            timeout=5,
        )


def run_dashboard(tree: TodoTree, store: TreeStore | None = None) -> None:
    """Run the dashboard application."""
    app = Dashboard(tree=tree, store=store)
    app.run()

