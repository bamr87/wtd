"""Fleet domain models.

A :class:`WorkItem` is a cross-repo todo: a unit of SDLC work discovered by
a scanner, an agent, or a human. Agents consume work items and may discover
new ones — that feedback loop is the platform's flywheel, so every item
carries provenance (``discovered_by``) and a stable ``dedup_key`` to keep
the loop convergent instead of explosive.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from wtd.core.models import TodoPriority


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkKind(str, Enum):
    """The kinds of SDLC work the fleet knows how to route."""

    TRIAGE_ISSUE = "triage_issue"  # review/label/prioritize an issue
    FIX_BUG = "fix_bug"  # analyze a reported bug, propose the fix
    REVIEW_PR = "review_pr"  # review an open pull request
    INVESTIGATE_CI = "investigate_ci"  # diagnose a failing workflow run
    WRITE_DOCS = "write_docs"  # write or improve documentation
    IMPROVE_CODE = "improve_code"  # act on TODO/FIXME debt in code
    WRITE_ARTICLE = "write_article"  # write an article/blog draft
    CUSTOM = "custom"  # human-injected free-form work


class WorkStatus(str, Enum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    DEFERRED = "deferred"  # no budget/capacity this cycle


class WorkItem(BaseModel):
    """A unit of fleet work ("todo") targeting one repository."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    dedup_key: str
    kind: WorkKind
    repo: str  # "owner/name"
    title: str
    description: str = ""
    url: str | None = None
    priority: TodoPriority = TodoPriority.MEDIUM
    status: WorkStatus = WorkStatus.QUEUED
    role_hint: str | None = None
    #: provenance: "scanner:<name>", "agent:<role>", or "manual"
    discovered_by: str = "manual"
    evidence: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()


class ActionType(str, Enum):
    """GitHub mutations an agent may request. Everything else is rejected."""

    COMMENT = "comment"  # comment on the item's issue/PR
    ADD_LABELS = "add_labels"  # label the item's issue/PR
    CREATE_ISSUE = "create_issue"  # file a new issue in the item's repo
    PROPOSE_PR = "propose_pr"  # open a draft PR with file changes
    MERGE_PR = "merge_pr"  # merge the item's PR — only through the merge gate


class ProposedAction(BaseModel):
    """A validated action an agent asked the platform to perform."""

    type: ActionType
    body: str = ""
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    branch: str = ""
    files: list[dict[str, str]] = Field(default_factory=list)  # {path, content}
    #: set by the dispatcher after execution
    applied: bool = False
    result_url: str | None = None
    error: str | None = None


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentRunRecord(BaseModel):
    """Ledger entry for one agent invocation."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    item_id: str
    dedup_key: str
    kind: WorkKind
    repo: str
    role: str
    provider: str = ""
    model: str = ""
    lane: str = ""
    outcome: RunOutcome = RunOutcome.SKIPPED
    dry_run: bool = True
    summary: str = ""
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    actions: list[ProposedAction] = Field(default_factory=list)
    discovered: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "item"


def make_dedup_key(repo: str, kind: WorkKind | str, anchor: str) -> str:
    """Stable identity of a work item.

    ``anchor`` is the durable reference: an issue/PR number, a workflow
    path, a file path, or a normalized title for free-form work. Re-running
    discovery must regenerate the same key for the same underlying work.
    """
    kind_value = kind.value if isinstance(kind, WorkKind) else str(kind)
    normalized = re.sub(r"\s+", " ", str(anchor)).strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{repo}:{kind_value}:{digest}"
