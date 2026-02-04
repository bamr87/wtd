"""
WTD Core Data Models
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    """Status of a TODO item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    COLLAPSED = "collapsed"  # Auto-removed when parent completes


class TodoContext(str, Enum):
    """Detected context type for a TODO."""
    BUGFIX = "bugfix"
    WRITE = "write"
    PLAN = "plan"
    LEARN = "learn"
    BUILD = "build"
    REFACTOR = "refactor"
    TEST = "test"
    DEPLOY = "deploy"
    UNKNOWN = "unknown"


class TodoPriority(str, Enum):
    """Priority levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TodoSource(BaseModel):
    """Source location of a discovered TODO."""
    file_path: Path | None = None
    line_number: int | None = None
    source_type: str = "file"  # file, github_issue, comment, note
    url: str | None = None
    raw_text: str = ""


class TodoNode(BaseModel):
    """A single TODO node in the recursive tree."""
    id: UUID = Field(default_factory=uuid4)
    parent_id: UUID | None = None
    title: str
    description: str = ""
    status: TodoStatus = TodoStatus.PENDING
    context: TodoContext = TodoContext.UNKNOWN
    priority: TodoPriority = TodoPriority.MEDIUM
    source: TodoSource | None = None
    
    # Recursion tracking
    depth: int = 0
    fitness_score: float = 1.0  # Decays with depth
    children_ids: list[UUID] = Field(default_factory=list)
    
    # Execution
    actions: list[dict[str, Any]] = Field(default_factory=list)
    result: str | None = None
    error: str | None = None
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    
    # Metadata
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_leaf(self) -> bool:
        """Check if this node has no children."""
        return len(self.children_ids) == 0

    def is_done(self) -> bool:
        """Check if this TODO is completed or cancelled."""
        return self.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED, TodoStatus.COLLAPSED)

    def can_spawn_children(self, max_depth: int = 7) -> bool:
        """Check if this node can spawn child tasks."""
        return (
            self.depth < max_depth 
            and self.fitness_score > 0.1 
            and not self.is_done()
        )


class WorkspaceConfig(BaseModel):
    """Configuration for workspace setup."""
    vscode_workspace: Path | None = None
    files_to_open: list[Path] = Field(default_factory=list)
    terminals: list[dict[str, Any]] = Field(default_factory=list)
    browser_urls: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)


class ScanResult(BaseModel):
    """Result of scanning for TODOs."""
    todos: list[TodoNode] = Field(default_factory=list)
    context: TodoContext = TodoContext.UNKNOWN
    confidence: float = 0.0
    workspace_config: WorkspaceConfig | None = None
    scan_duration_ms: int = 0
    sources_scanned: int = 0


class ExecutionResult(BaseModel):
    """Result of executing a TODO action."""
    success: bool
    action_type: str
    output: str = ""
    error: str | None = None
    spawned_todos: list[TodoNode] = Field(default_factory=list)
    duration_ms: int = 0



