"""
WTD Recursive TODO Tree - The core engine
"""

from datetime import datetime
from typing import Callable
from uuid import UUID

from wtd.config import get_config
from wtd.core.models import (
    ExecutionResult,
    TodoContext,
    TodoNode,
    TodoPriority,
    TodoStatus,
)


class TodoTree:
    """
    The recursive TODO tree engine.
    
    Manages a tree of TodoNodes where each node can spawn children,
    and completing all children collapses the parent.
    """

    def __init__(self):
        self._nodes: dict[UUID, TodoNode] = {}
        self._root_ids: list[UUID] = []
        self._config = get_config()

    @property
    def all_nodes(self) -> list[TodoNode]:
        """Get all nodes in the tree."""
        return list(self._nodes.values())

    @property
    def root_nodes(self) -> list[TodoNode]:
        """Get all root-level nodes."""
        return [self._nodes[nid] for nid in self._root_ids if nid in self._nodes]

    @property
    def pending_nodes(self) -> list[TodoNode]:
        """Get all pending (actionable) nodes."""
        return [n for n in self._nodes.values() if n.status == TodoStatus.PENDING]

    @property
    def in_progress_nodes(self) -> list[TodoNode]:
        """Get all in-progress nodes."""
        return [n for n in self._nodes.values() if n.status == TodoStatus.IN_PROGRESS]

    def add_root(self, node: TodoNode) -> TodoNode:
        """Add a root-level TODO."""
        node.depth = 0
        node.parent_id = None
        node.fitness_score = 1.0
        self._nodes[node.id] = node
        self._root_ids.append(node.id)
        return node

    def add_todos(self, nodes: list[TodoNode]) -> list[TodoNode]:
        """Add multiple root-level TODOs."""
        return [self.add_root(node) for node in nodes]

    def spawn_child(
        self,
        parent_id: UUID,
        title: str,
        description: str = "",
        context: TodoContext | None = None,
        priority: TodoPriority | None = None,
    ) -> TodoNode | None:
        """
        Spawn a child TODO from a parent.
        
        Applies fitness decay based on depth.
        Returns None if max depth exceeded or fitness too low.
        """
        parent = self._nodes.get(parent_id)
        if parent is None:
            return None

        if not parent.can_spawn_children(self._config.max_recursion_depth):
            return None

        # Calculate child's fitness score with decay
        child_fitness = parent.fitness_score * (1 - self._config.fitness_decay_rate)

        child = TodoNode(
            parent_id=parent_id,
            title=title,
            description=description,
            status=TodoStatus.PENDING,
            context=context or parent.context,
            priority=priority or parent.priority,
            depth=parent.depth + 1,
            fitness_score=child_fitness,
        )

        self._nodes[child.id] = child
        parent.children_ids.append(child.id)

        return child

    def spawn_children(
        self,
        parent_id: UUID,
        children_data: list[dict],
    ) -> list[TodoNode]:
        """Spawn multiple children at once."""
        children = []
        for data in children_data:
            child = self.spawn_child(parent_id, **data)
            if child:
                children.append(child)
        return children

    def get_node(self, node_id: UUID) -> TodoNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_children(self, node_id: UUID) -> list[TodoNode]:
        """Get all children of a node."""
        node = self._nodes.get(node_id)
        if node is None:
            return []
        return [self._nodes[cid] for cid in node.children_ids if cid in self._nodes]

    def get_ancestors(self, node_id: UUID) -> list[TodoNode]:
        """Get all ancestors of a node (parent chain to root)."""
        ancestors = []
        node = self._nodes.get(node_id)
        while node and node.parent_id:
            parent = self._nodes.get(node.parent_id)
            if parent:
                ancestors.append(parent)
                node = parent
            else:
                break
        return ancestors

    def get_descendants(self, node_id: UUID) -> list[TodoNode]:
        """Get all descendants of a node (all children recursively)."""
        descendants = []
        node = self._nodes.get(node_id)
        if node is None:
            return descendants

        for child_id in node.children_ids:
            child = self._nodes.get(child_id)
            if child:
                descendants.append(child)
                descendants.extend(self.get_descendants(child_id))

        return descendants

    def start_node(self, node_id: UUID) -> bool:
        """Mark a node as in progress."""
        node = self._nodes.get(node_id)
        if node is None or node.is_done():
            return False
        
        node.status = TodoStatus.IN_PROGRESS
        node.started_at = datetime.now()
        return True

    def complete_node(
        self,
        node_id: UUID,
        result: str | None = None,
        collapse_children: bool = True,
    ) -> bool:
        """
        Mark a node as completed.
        
        If collapse_children is True, all descendant nodes are collapsed.
        Checks if parent can be auto-completed.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return False

        node.status = TodoStatus.COMPLETED
        node.completed_at = datetime.now()
        node.result = result

        # Collapse all children
        if collapse_children:
            for descendant in self.get_descendants(node_id):
                if not descendant.is_done():
                    descendant.status = TodoStatus.COLLAPSED
                    descendant.completed_at = datetime.now()

        # Check if parent can be auto-completed
        self._try_complete_parent(node_id)

        return True

    def fail_node(self, node_id: UUID, error: str) -> bool:
        """Mark a node as failed/blocked."""
        node = self._nodes.get(node_id)
        if node is None:
            return False

        node.status = TodoStatus.BLOCKED
        node.error = error
        return True

    def cancel_node(self, node_id: UUID, cascade: bool = True) -> bool:
        """Cancel a node and optionally its descendants."""
        node = self._nodes.get(node_id)
        if node is None:
            return False

        node.status = TodoStatus.CANCELLED
        node.completed_at = datetime.now()

        if cascade:
            for descendant in self.get_descendants(node_id):
                if not descendant.is_done():
                    descendant.status = TodoStatus.CANCELLED
                    descendant.completed_at = datetime.now()

        return True

    def _try_complete_parent(self, child_id: UUID) -> None:
        """Check if parent should be auto-completed after child completion."""
        child = self._nodes.get(child_id)
        if child is None or child.parent_id is None:
            return

        parent = self._nodes.get(child.parent_id)
        if parent is None or parent.is_done():
            return

        # Check if all children are done
        children = self.get_children(parent.id)
        all_done = all(c.is_done() for c in children)

        if all_done and children:
            # Auto-complete parent
            parent.status = TodoStatus.COMPLETED
            parent.completed_at = datetime.now()
            parent.result = f"All {len(children)} subtasks completed"
            
            # Recursively check grandparent
            self._try_complete_parent(parent.id)

    def get_next_actionable(self) -> TodoNode | None:
        """Get the next actionable TODO (highest priority pending leaf)."""
        candidates = [
            n for n in self._nodes.values()
            if n.status == TodoStatus.PENDING and n.is_leaf()
        ]
        
        if not candidates:
            # No leaves, get highest priority pending
            candidates = self.pending_nodes
        
        if not candidates:
            return None

        # Sort by priority and fitness
        priority_order = {
            TodoPriority.CRITICAL: 0,
            TodoPriority.HIGH: 1,
            TodoPriority.MEDIUM: 2,
            TodoPriority.LOW: 3,
        }
        
        candidates.sort(
            key=lambda n: (priority_order.get(n.priority, 2), -n.fitness_score)
        )
        
        return candidates[0]

    def get_progress(self) -> dict:
        """Get overall progress statistics."""
        total = len(self._nodes)
        if total == 0:
            return {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "blocked": 0,
                "cancelled": 0,
                "percentage": 0.0,
            }

        status_counts = {status: 0 for status in TodoStatus}
        for node in self._nodes.values():
            status_counts[node.status] += 1

        done_count = (
            status_counts[TodoStatus.COMPLETED]
            + status_counts[TodoStatus.COLLAPSED]
            + status_counts[TodoStatus.CANCELLED]
        )

        return {
            "total": total,
            "completed": status_counts[TodoStatus.COMPLETED],
            "in_progress": status_counts[TodoStatus.IN_PROGRESS],
            "pending": status_counts[TodoStatus.PENDING],
            "blocked": status_counts[TodoStatus.BLOCKED],
            "cancelled": status_counts[TodoStatus.CANCELLED],
            "collapsed": status_counts[TodoStatus.COLLAPSED],
            "percentage": (done_count / total) * 100,
        }

    def to_dict(self) -> dict:
        """Serialize tree to dictionary."""
        return {
            "nodes": {str(k): v.model_dump() for k, v in self._nodes.items()},
            "root_ids": [str(rid) for rid in self._root_ids],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TodoTree":
        """Deserialize tree from dictionary."""
        tree = cls()
        for node_id, node_data in data.get("nodes", {}).items():
            node = TodoNode(**node_data)
            tree._nodes[UUID(node_id)] = node
        tree._root_ids = [UUID(rid) for rid in data.get("root_ids", [])]
        return tree

    def print_tree(self, node_id: UUID | None = None, indent: int = 0) -> str:
        """Generate a text representation of the tree."""
        lines = []
        
        if node_id is None:
            # Print all roots
            for root_id in self._root_ids:
                lines.append(self.print_tree(root_id, 0))
        else:
            node = self._nodes.get(node_id)
            if node:
                status_icons = {
                    TodoStatus.PENDING: "○",
                    TodoStatus.IN_PROGRESS: "◐",
                    TodoStatus.COMPLETED: "●",
                    TodoStatus.BLOCKED: "✗",
                    TodoStatus.CANCELLED: "⊘",
                    TodoStatus.COLLAPSED: "◌",
                }
                icon = status_icons.get(node.status, "?")
                prefix = "  " * indent
                lines.append(f"{prefix}{icon} {node.title} [{node.context.value}]")
                
                for child_id in node.children_ids:
                    lines.append(self.print_tree(child_id, indent + 1))

        return "\n".join(lines)

