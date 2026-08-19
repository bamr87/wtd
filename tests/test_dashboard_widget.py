"""Regression tests for the Textual dashboard widgets."""

from __future__ import annotations

from wtd.core.models import TodoNode, TodoStatus
from wtd.core.tree import TodoTree
from wtd.ui.dashboard import TodoTreeWidget


def _tree_with_children() -> TodoTree:
    tree = TodoTree()
    root = tree.add_root(TodoNode(title="Parent task"))
    tree.spawn_child(root.id, title="Child task")
    return tree


def test_widget_constructs() -> None:
    """The widget must not shadow Textual's ``Tree._add_node``.

    Regression: the TODO-adding helper was named ``_add_node``, which is a
    Textual ``Tree`` internal invoked as ``self._add_node(parent, label,
    data)`` from ``Tree.__init__``. The clashing two-argument override made
    constructing the widget raise TypeError, so ``wtd dashboard`` could
    never open.
    """
    widget = TodoTreeWidget(_tree_with_children())
    assert widget.root is not None


def test_rebuild_tree_populates_nodes_and_map() -> None:
    """Rebuilding renders every TODO and maps display nodes back to TODO ids."""
    tree = _tree_with_children()
    widget = TodoTreeWidget(tree)

    widget.rebuild_tree()

    assert len(widget.root.children) == 1
    assert "Parent task" in str(widget.root.children[0].label)
    # Parent + child are both mapped back to their TodoNode ids.
    assert len(widget.node_map) == 2
    assert set(widget.node_map.values()) == {n.id for n in tree.all_nodes}


def test_rebuild_tree_is_idempotent() -> None:
    """Repeated rebuilds must not duplicate rendered nodes."""
    widget = TodoTreeWidget(_tree_with_children())

    widget.rebuild_tree()
    widget.rebuild_tree()

    assert len(widget.root.children) == 1
    assert len(widget.node_map) == 2


def test_completed_todo_renders_status_icon() -> None:
    """Status is reflected in the rendered label."""
    tree = TodoTree()
    node = tree.add_root(TodoNode(title="Done task"))
    tree.complete_node(node.id)

    widget = TodoTreeWidget(tree)
    widget.rebuild_tree()

    label = str(widget.root.children[0].label)
    assert "Done task" in label
    assert tree.get_node(node.id).status == TodoStatus.COMPLETED
