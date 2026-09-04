"""Parse and validate agent output into safe, executable actions.

Agents reply with one JSON object: a summary, a list of requested actions,
and a list of discovered work items. Everything is validated against the
role's grants and hard platform limits before anything touches GitHub —
an agent can only ever do what its role declares, at bounded volume.

Path safety is absolute: proposed PRs may never write outside the repo,
and never into ``.github/workflows/`` (a workflow write is a privilege
escalation — the fleet's own credentials would run it).
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field

from wtd.core.agent import extract_json_block
from wtd.core.models import TodoPriority
from wtd.fleet.models import (
    ActionType,
    ProposedAction,
    WorkItem,
    WorkKind,
    make_dedup_key,
    slugify,
)
from wtd.fleet.roles import AgentRole

MAX_ACTIONS_PER_RUN = 3
MAX_COMMENT_CHARS = 10_000
MAX_ISSUE_BODY_CHARS = 12_000
MAX_LABELS = 5
MAX_PR_FILES = 8
MAX_FILE_CHARS = 120_000
MAX_TITLE_CHARS = 200
BRANCH_PREFIX = "wtd/"

_ALLOWED_DISCOVERED_KINDS = {
    WorkKind.TRIAGE_ISSUE,
    WorkKind.FIX_BUG,
    WorkKind.WRITE_DOCS,
    WorkKind.IMPROVE_CODE,
    WorkKind.WRITE_ARTICLE,
    WorkKind.CUSTOM,
}


class OutcomeError(ValueError):
    """The agent's reply could not be turned into a valid outcome."""


@dataclass
class Outcome:
    summary: str
    actions: list[ProposedAction] = field(default_factory=list)
    discovered: list[WorkItem] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # human-readable reasons


def output_contract(role: AgentRole) -> str:
    """The response-format section appended to every fleet prompt."""
    shapes = {
        ActionType.COMMENT: '{"type": "comment", "body": "markdown"}',
        ActionType.ADD_LABELS: '{"type": "add_labels", "labels": ["bug"]}',
        ActionType.CREATE_ISSUE: (
            '{"type": "create_issue", "title": "...", "body": "markdown"}'
        ),
        ActionType.PROPOSE_PR: (
            '{"type": "propose_pr", "title": "...", "body": "markdown", '
            '"branch": "wtd/short-slug", '
            '"files": [{"path": "relative/path.md", "content": "full file content"}]}'
        ),
        ActionType.MERGE_PR: (
            '{"type": "merge_pr", "body": "why this change is safe to merge"}'
        ),
    }
    allowed = [shapes[a] for a in role.allowed_actions]
    action_lines = "\n".join(f"  {shape}" for shape in allowed) or "  (none permitted)"
    # The merge caveat is only shown to roles that can actually merge —
    # every other role would just be reading about a door it cannot open.
    merge_rule = (
        """
- "merge_pr" is a RECOMMENDATION, not a command: the platform re-verifies
  CI, mergeability, and policy before merging, and refuses if anything is
  off. Request it only when you would merge the change yourself, and always
  alongside the comment that says why."""
        if ActionType.MERGE_PR in role.allowed_actions
        else ""
    )
    return f"""
RESPONSE FORMAT — reply with ONLY this JSON object, no prose around it:
{{
  "summary": "one or two sentences on what you concluded",
  "actions": [ ... zero or more of the permitted shapes below ... ],
  "discovered": [
    {{"kind": "write_docs|fix_bug|improve_code|write_article|custom",
      "title": "...", "description": "...", "priority": "high|medium|low"}}
  ]
}}

Permitted action shapes for your role ({role.name}):
{action_lines}

Rules:
- At most {MAX_ACTIONS_PER_RUN} actions. An empty actions list is a valid,
  often correct, outcome.
- "discovered" is for NEW work you noticed that is out of scope for this
  run. Do not rediscover the task you were given.{merge_rule}
"""


def _safe_rel_path(path: str) -> str | None:
    """Normalize a proposed file path; None when it escapes or is forbidden."""
    if not path or path.startswith(("/", "\\")) or "\\" in path:
        return None
    normalized = posixpath.normpath(path)
    if normalized.startswith("..") or "/../" in f"/{normalized}/":
        return None
    if normalized in (".", ""):
        return None
    if normalized.startswith(".github/workflows"):
        return None  # never let an agent write workflow files
    if ".git/" in f"{normalized}/":
        return None
    return normalized


def _validate_action(raw: dict, role: AgentRole, item: WorkItem) -> ProposedAction | str:
    """Return a validated action, or a rejection reason string."""
    try:
        action_type = ActionType(str(raw.get("type", "")))
    except ValueError:
        return f"unknown action type {raw.get('type')!r}"
    if action_type not in role.allowed_actions:
        return f"action {action_type.value!r} not granted to role {role.name!r}"

    if action_type == ActionType.COMMENT:
        body = str(raw.get("body", "")).strip()
        if not body:
            return "comment with empty body"
        return ProposedAction(type=action_type, body=body[:MAX_COMMENT_CHARS])

    if action_type == ActionType.ADD_LABELS:
        labels = [str(lbl).strip()[:50] for lbl in raw.get("labels", []) if str(lbl).strip()]
        if not labels:
            return "add_labels with no labels"
        return ProposedAction(type=action_type, labels=labels[:MAX_LABELS])

    if action_type == ActionType.CREATE_ISSUE:
        title = str(raw.get("title", "")).strip()[:MAX_TITLE_CHARS]
        body = str(raw.get("body", "")).strip()[:MAX_ISSUE_BODY_CHARS]
        if not title:
            return "create_issue with empty title"
        return ProposedAction(type=action_type, title=title, body=body)

    if action_type == ActionType.PROPOSE_PR:
        title = str(raw.get("title", "")).strip()[:MAX_TITLE_CHARS]
        if not title:
            return "propose_pr with empty title"
        body = str(raw.get("body", "")).strip()[:MAX_ISSUE_BODY_CHARS]
        branch = str(raw.get("branch", "")).strip() or f"{BRANCH_PREFIX}{slugify(title)}"
        if not branch.startswith(BRANCH_PREFIX):
            branch = f"{BRANCH_PREFIX}{slugify(branch)}"
        branch = branch[:80]
        files_raw = raw.get("files", [])
        if not isinstance(files_raw, list) or not files_raw:
            return "propose_pr with no files"
        if len(files_raw) > MAX_PR_FILES:
            return f"propose_pr with {len(files_raw)} files (max {MAX_PR_FILES})"
        files: list[dict[str, str]] = []
        for entry in files_raw:
            if not isinstance(entry, dict):
                return "propose_pr file entry is not an object"
            path = _safe_rel_path(str(entry.get("path", "")))
            content = entry.get("content")
            if path is None:
                return f"unsafe or forbidden file path {entry.get('path')!r}"
            if not isinstance(content, str) or not content:
                return f"file {path} has no content"
            if len(content) > MAX_FILE_CHARS:
                return f"file {path} exceeds {MAX_FILE_CHARS} chars"
            files.append({"path": path, "content": content})
        return ProposedAction(
            type=action_type, title=title, body=body, branch=branch, files=files
        )

    if action_type == ActionType.MERGE_PR:
        body = str(raw.get("body", "")).strip()
        if not body:
            # A merge with no stated rationale is unreviewable after the
            # fact; the body is what lands in the merge comment.
            return "merge_pr without a rationale body"
        return ProposedAction(type=action_type, body=body[:MAX_COMMENT_CHARS])

    return f"unhandled action type {action_type.value!r}"  # pragma: no cover


def _validate_discovered(
    raw: dict, item: WorkItem, role: AgentRole, max_items: int, count: int
) -> WorkItem | str:
    if count >= max_items:
        return "discovered item beyond per-run cap"
    try:
        kind = WorkKind(str(raw.get("kind", "")))
    except ValueError:
        return f"unknown discovered kind {raw.get('kind')!r}"
    if kind not in _ALLOWED_DISCOVERED_KINDS:
        return f"kind {kind.value!r} cannot be agent-discovered"
    title = str(raw.get("title", "")).strip()[:MAX_TITLE_CHARS]
    if not title:
        return "discovered item with empty title"
    priority_map = {
        "critical": TodoPriority.HIGH,  # agents don't get to declare criticals
        "high": TodoPriority.HIGH,
        "medium": TodoPriority.MEDIUM,
        "low": TodoPriority.LOW,
    }
    priority = priority_map.get(str(raw.get("priority", "medium")).lower(), TodoPriority.MEDIUM)
    return WorkItem(
        dedup_key=make_dedup_key(item.repo, kind, title),
        kind=kind,
        repo=item.repo,
        title=title,
        description=str(raw.get("description", "")).strip()[:2000],
        priority=priority,
        discovered_by=f"agent:{role.name}",
        evidence={"parent_item": item.dedup_key},
    )


def parse_outcome(
    text: str,
    role: AgentRole,
    item: WorkItem,
    *,
    max_discovered: int = 5,
) -> Outcome:
    """Parse the agent's reply. Raises OutcomeError when unusable."""
    try:
        payload = json.loads(extract_json_block(text))
    except json.JSONDecodeError as exc:
        raise OutcomeError(f"reply was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OutcomeError("reply JSON must be an object")

    outcome = Outcome(summary=str(payload.get("summary", "")).strip()[:2000])

    actions_raw = payload.get("actions", [])
    if not isinstance(actions_raw, list):
        raise OutcomeError("'actions' must be a list")
    for raw in actions_raw[: MAX_ACTIONS_PER_RUN * 2]:
        if len(outcome.actions) >= MAX_ACTIONS_PER_RUN:
            outcome.rejected.append("action beyond per-run cap")
            continue
        if not isinstance(raw, dict):
            outcome.rejected.append("action entry is not an object")
            continue
        result = _validate_action(raw, role, item)
        if isinstance(result, str):
            outcome.rejected.append(result)
        else:
            outcome.actions.append(result)

    discovered_raw = payload.get("discovered", [])
    if isinstance(discovered_raw, list):
        for raw in discovered_raw[: max_discovered * 2]:
            if not isinstance(raw, dict):
                outcome.rejected.append("discovered entry is not an object")
                continue
            found = _validate_discovered(
                raw, item, role, max_discovered, len(outcome.discovered)
            )
            if isinstance(found, str):
                outcome.rejected.append(found)
            else:
                outcome.discovered.append(found)

    return outcome
