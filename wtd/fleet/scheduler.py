"""Pure scheduling: match queued work to agent roles, fairly.

Given the pending queue and the loaded roles, ``plan_cycle`` produces the
ordered assignments for one orchestrator cycle:

1. Narrow the role registry to what the repository permits: a roster
   entry's ``roles: [...]`` list is an allowlist, and an empty (or absent)
   list means every enabled role.
2. Resolve a role per item from that narrowed registry (``role_hint``
   first, else the first role handling the kind); items no *permitted*
   role handles are reported as unroutable.
3. Order by priority, then age (oldest first).
4. Interleave repositories round-robin inside each priority band so one
   noisy repo cannot starve the rest of the fleet.
5. Cut off at ``max_runs``.

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


def permitted_roles(
    roles: dict[str, AgentRole], allowed: list[str] | None
) -> dict[str, AgentRole]:
    """The role registry as one repository sees it.

    ``allowed`` is that repository's roster allowlist. Empty or absent
    means "every enabled role" — the documented default — so only a
    non-empty list narrows anything. Names that match no loaded role are
    simply absent from the result, which strands that repo's work as
    unroutable rather than quietly widening its blast radius.
    """
    if not allowed:
        return roles
    permitted = set(allowed)
    return {name: role for name, role in roles.items() if name in permitted}


def unknown_role_names(
    roles: dict[str, AgentRole], repo_roles: dict[str, list[str]] | None
) -> dict[str, list[str]]:
    """Roster role names that match no loaded role, per repository.

    A typo here is silent otherwise: the repo simply stops being worked.
    Callers surface this; the scheduler itself stays pure.
    """
    if not repo_roles:
        return {}
    unknown = {
        repo: sorted(name for name in allowed if name not in roles)
        for repo, allowed in repo_roles.items()
    }
    return {repo: names for repo, names in unknown.items() if names}


def plan_cycle(
    items: list[WorkItem],
    roles: dict[str, AgentRole],
    *,
    max_runs: int,
    repos: list[str] | None = None,
    role_names: list[str] | None = None,
    repo_roles: dict[str, list[str]] | None = None,
) -> CyclePlan:
    """Build the assignment plan for one cycle.

    ``repo_roles`` maps a repository to the roles its roster entry
    permits (see :func:`permitted_roles`). ``role_names`` is the caller's
    one-off ``--role`` filter and narrows further; neither can widen what
    the roster allows.
    """
    wanted_repos = set(repos) if repos else None
    wanted_roles = set(role_names) if role_names else None

    # One narrowed registry per repository, not per item.
    registries: dict[str, dict[str, AgentRole]] = {}

    routable: list[tuple[WorkItem, AgentRole]] = []
    unroutable: list[WorkItem] = []
    for item in items:
        if wanted_repos is not None and item.repo not in wanted_repos:
            continue
        if item.repo not in registries:
            registries[item.repo] = permitted_roles(
                roles, (repo_roles or {}).get(item.repo)
            )
        role = role_for_kind(registries[item.repo], item.kind, item.role_hint)
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
