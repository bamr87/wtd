"""wtd.fleet — the autonomous AI agent fleet platform.

Subsystems:

- :mod:`wtd.fleet.models`       work items, actions, run records
- :mod:`wtd.fleet.settings`     wtd.yml roster + tunables
- :mod:`wtd.fleet.state`        persistent queue + run ledger
- :mod:`wtd.fleet.github`       minimal GitHub REST client
- :mod:`wtd.fleet.discovery`    deterministic work scanners
- :mod:`wtd.fleet.roles`        agent role registry (+ agents/*.md overrides)
- :mod:`wtd.fleet.balancer`     token-capacity load balancing across lanes
- :mod:`wtd.fleet.scheduler`    pure work↔role matching with fairness
- :mod:`wtd.fleet.context`      per-kind evidence builders
- :mod:`wtd.fleet.outcome`      structured agent-output validation
- :mod:`wtd.fleet.dispatcher`   one agent run end-to-end (apply-gated)
- :mod:`wtd.fleet.orchestrator` the cycle/loop mechanism
- :mod:`wtd.fleet.monitor`      status aggregation
"""

from wtd.fleet.models import (
    ActionType,
    AgentRunRecord,
    ProposedAction,
    RunOutcome,
    WorkItem,
    WorkKind,
    WorkStatus,
    make_dedup_key,
)
from wtd.fleet.orchestrator import CycleReport, FleetOrchestrator
from wtd.fleet.settings import FleetSettings, load_settings
from wtd.fleet.state import FleetState

__all__ = [
    "ActionType",
    "AgentRunRecord",
    "CycleReport",
    "FleetOrchestrator",
    "FleetSettings",
    "FleetState",
    "ProposedAction",
    "RunOutcome",
    "WorkItem",
    "WorkKind",
    "WorkStatus",
    "load_settings",
    "make_dedup_key",
]
