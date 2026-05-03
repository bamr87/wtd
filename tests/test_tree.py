"""Tests for ``wtd.core.tree``."""

from __future__ import annotations

from wtd.core.models import TodoContext, TodoNode, TodoPriority, TodoStatus
from wtd.core.tree import TodoTree


def _root(title: str = "root") -> TodoNode:
    return TodoNode(
        title=title,
        description="",
        status=TodoStatus.PENDING,
        context=TodoContext.BUILD,
        priority=TodoPriority.MEDIUM,
    )


def test_add_root_resets_depth_and_fitness() -> None:
    """``add_root`` should normalise depth/parent/fitness regardless of input."""
    tree = TodoTree()
    n = TodoNode(title="weird", depth=99, fitness_score=0.01)
    added = tree.add_root(n)

    assert added.depth == 0
    assert added.parent_id is None
    assert added.fitness_score == 1.0
    assert tree.root_nodes == [added]
    assert tree.all_nodes == [added]


def test_spawn_child_decays_fitness_and_increments_depth() -> None:
    """Children should inherit context/priority and have decayed fitness."""
    tree = TodoTree()
    parent = tree.add_root(_root())

    child = tree.spawn_child(parent.id, title="child task one")
    assert child is not None
    assert child.depth == parent.depth + 1
    assert child.fitness_score < parent.fitness_score
    assert child.parent_id == parent.id
    assert child.id in parent.children_ids
    assert child.context == parent.context
    assert child.priority == parent.priority


def test_spawn_child_respects_max_depth() -> None:
    """Spawning beyond the configured max depth should return None."""
    tree = TodoTree()
    max_depth = tree._config.max_recursion_depth

    current = tree.add_root(_root())
    # Build a chain at the depth limit; the next spawn must fail.
    for i in range(max_depth):
        child = tree.spawn_child(current.id, title=f"child {i}")
        assert child is not None, f"failed to spawn at depth {i}"
        current = child

    # `current` is now at max_depth, so its children would exceed the cap.
    overflow = tree.spawn_child(current.id, title="too deep")
    assert overflow is None


def test_completing_only_child_auto_completes_parent() -> None:
    """If all children of a node finish, the parent should auto-complete."""
    tree = TodoTree()
    parent = tree.add_root(_root())
    child = tree.spawn_child(parent.id, title="only child task")
    assert child is not None

    assert tree.complete_node(child.id) is True

    refreshed_parent = tree.get_node(parent.id)
    assert refreshed_parent is not None
    assert refreshed_parent.status == TodoStatus.COMPLETED


def test_completing_parent_collapses_descendants() -> None:
    """Completing a parent with ``collapse_children`` collapses the subtree."""
    tree = TodoTree()
    parent = tree.add_root(_root())
    a = tree.spawn_child(parent.id, title="child a")
    assert a is not None
    b = tree.spawn_child(a.id, title="grandchild b")
    assert b is not None

    assert tree.complete_node(parent.id, collapse_children=True) is True

    a_after = tree.get_node(a.id)
    b_after = tree.get_node(b.id)
    assert a_after is not None and a_after.status == TodoStatus.COLLAPSED
    assert b_after is not None and b_after.status == TodoStatus.COLLAPSED


def test_cancel_cascades_to_descendants() -> None:
    """Cancelling with ``cascade=True`` cancels all descendants too."""
    tree = TodoTree()
    parent = tree.add_root(_root())
    a = tree.spawn_child(parent.id, title="cancel me a")
    assert a is not None
    b = tree.spawn_child(a.id, title="cancel me b")
    assert b is not None

    assert tree.cancel_node(parent.id, cascade=True) is True

    for n in (parent, a, b):
        refreshed = tree.get_node(n.id)
        assert refreshed is not None
        assert refreshed.status == TodoStatus.CANCELLED


def test_get_ancestors_returns_chain_to_root() -> None:
    tree = TodoTree()
    parent = tree.add_root(_root())
    a = tree.spawn_child(parent.id, title="middle node here")
    assert a is not None
    b = tree.spawn_child(a.id, title="leaf node here")
    assert b is not None

    ancestors = tree.get_ancestors(b.id)
    assert [n.id for n in ancestors] == [a.id, parent.id]


def test_get_next_actionable_prefers_pending_leaves() -> None:
    tree = TodoTree()
    parent = tree.add_root(_root())
    leaf = tree.spawn_child(parent.id, title="leaf actionable item")
    assert leaf is not None

    nxt = tree.get_next_actionable()
    assert nxt is not None
    # Leaf is the only PENDING leaf; the parent is also PENDING but not a leaf.
    assert nxt.id == leaf.id


def test_unknown_node_operations_are_safe() -> None:
    """Operating on unknown UUIDs should return False, not raise."""
    tree = TodoTree()
    parent = tree.add_root(_root())
    unrelated = TodoNode(title="not in tree").id

    assert tree.complete_node(unrelated) is False
    assert tree.cancel_node(unrelated) is False
    assert tree.fail_node(unrelated, "nope") is False
    assert tree.spawn_child(unrelated, title="orphan child") is None
    assert tree.get_node(unrelated) is None
    # Sanity: the real node is still intact.
    assert tree.get_node(parent.id) is not None
