"""Pure scheduling: match queued work to agent roles, fairly.

Given the pending queue and the loaded roles, ``plan_cycle`` produces the
ordered assignments for one orchestrator cycle:

1. Resolve a role per item (``role_hint`` first, else first role handling
   the kind); items nobody handles are reported as unroutable.
2. Order by priority, then age (oldest first).
3. Interleave repositories round-robin inside each priority band so one
   noisy repo cannot starve the rest of the fleet.
4. Cut off at ``max_runs``.

No I/O, no clock, no randomness — everything is testable and the
orchestrator applies budgets separately at dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass

from wtd.core.models import TodoPriority
from wtd.fleet.models import WorkItem
from wtd.fleet.roles import AgentRole, role_for_kind

_PRIORITY_RANK = {
    TodoPriority.CRITICAL: 0,
    TodoPriority.HIGH: 1,
    TodoPriority.MEDIUM: 2,
    TodoPriority.LOW: 3,
}


@dataclass
class Assignment:
    item: WorkItem
    role: AgentRole


@dataclass
class CyclePlan:
    assignments: list[Assignment]
    unroutable: list[WorkItem]
    overflow: list[WorkItem]  # routable but beyond max_runs this cycle


def _fair_interleave(items: list[WorkItem]) -> list[WorkItem]:
    """Round-robin across repos, preserving relative order within a repo."""
    by_repo: dict[str, list[WorkItem]] = {}
    repo_order: list[str] = []
    for item in items:
        if item.repo not in by_repo:
            by_repo[item.repo] = []
            repo_order.append(item.repo)
        by_repo[item.repo].append(item)

    interleaved: list[WorkItem] = []
    while any(by_repo.values()):
        for repo in repo_order:
            bucket = by_repo[repo]
            if bucket:
                interleaved.append(bucket.pop(0))
    return interleaved


def plan_cycle(
    items: list[WorkItem],
    roles: dict[str, AgentRole],
    *,
    max_runs: int,
    repos: list[str] | None = None,
    role_names: list[str] | None = None,
) -> CyclePlan:
    """Build the assignment plan for one cycle."""
    wanted_repos = set(repos) if repos else None
    wanted_roles = set(role_names) if role_names else None

    routable: list[tuple[WorkItem, AgentRole]] = []
    unroutable: list[WorkItem] = []
    for item in items:
        if wanted_repos is not None and item.repo not in wanted_repos:
            continue
        role = role_for_kind(roles, item.kind, item.role_hint)
        if role is None or (wanted_roles is not None and role.name not in wanted_roles):
            unroutable.append(item)
            continue
        routable.append((item, role))

    # Priority bands, oldest-first inside each, repos interleaved per band.
    ordered: list[tuple[WorkItem, AgentRole]] = []
    role_by_item = {item.id: role for item, role in routable}
    for rank in sorted(set(_PRIORITY_RANK.values())):
        band = [
            item
            for item, _ in routable
            if _PRIORITY_RANK.get(item.priority, 2) == rank
        ]
        band.sort(key=lambda i: i.created_at)
        for item in _fair_interleave(band):
            ordered.append((item, role_by_item[item.id]))

    assignments = [Assignment(item, role) for item, role in ordered[:max_runs]]
    overflow = [item for item, _ in ordered[max_runs:]]
    return CyclePlan(assignments=assignments, unroutable=unroutable, overflow=overflow)
