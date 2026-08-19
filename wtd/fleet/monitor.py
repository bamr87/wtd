"""Fleet monitoring: aggregate queue, budget, and run health into one view.

Pure aggregation over persisted state — usable from the CLI, the REST API,
and tests without touching the network.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from wtd.config import WTDConfig, get_config
from wtd.fleet.balancer import CapacityBalancer, default_lanes
from wtd.fleet.models import RunOutcome
from wtd.fleet.settings import FleetSettings, load_settings
from wtd.fleet.state import FleetState
from wtd.providers import describe_chain


def fleet_status(
    config: WTDConfig | None = None,
    settings: FleetSettings | None = None,
    state: FleetState | None = None,
    balancer: CapacityBalancer | None = None,
    *,
    recent_runs: int = 10,
) -> dict[str, Any]:
    """One status document describing the whole platform."""
    config = config or get_config()
    settings = settings or load_settings(config)
    state_dir = config.fleet_state_path
    state = state or FleetState(state_dir).load()
    balancer = balancer or CapacityBalancer(
        default_lanes(settings), state_dir / "capacity.json"
    )

    items = list(state.items.values())
    by_kind: dict[str, int] = {}
    by_repo: dict[str, int] = {}
    for item in items:
        by_kind[item.kind.value] = by_kind.get(item.kind.value, 0) + 1
        by_repo[item.repo] = by_repo.get(item.repo, 0) + 1

    runs = state.recent_runs(recent_runs)
    completed = sum(1 for r in runs if r.outcome == RunOutcome.COMPLETED)
    failed = sum(1 for r in runs if r.outcome == RunOutcome.FAILED)

    return {
        "enabled": config.fleet_enabled,
        "apply": config.fleet_apply,
        "provider_chain": describe_chain(config),
        "roster": {
            "repos": settings.repo_slugs(),
            "roles_enabled": settings.roles_enabled or "all",
            "config_file": str(settings.source_path) if settings.source_path else None,
        },
        "queue": {
            "total": len(items),
            "by_status": state.counts(),
            "by_kind": by_kind,
            "by_repo": by_repo,
        },
        "lanes": [asdict(snapshot) for snapshot in balancer.snapshot()],
        "recent_runs": {
            "shown": len(runs),
            "completed": completed,
            "failed": failed,
            "runs": [
                {
                    "id": r.id,
                    "role": r.role,
                    "kind": r.kind.value,
                    "repo": r.repo,
                    "outcome": r.outcome.value,
                    "lane": r.lane,
                    "model": r.model,
                    "tokens": r.input_tokens + r.output_tokens,
                    "cost_usd": round(r.cost_usd, 4),
                    "dry_run": r.dry_run,
                    "actions_applied": sum(1 for a in r.actions if a.applied),
                    "discovered": r.discovered,
                    "summary": r.summary[:160],
                    "started_at": r.started_at.isoformat(),
                }
                for r in runs
            ],
        },
        "guardrails": {
            "max_runs_per_cycle": settings.max_runs_per_cycle,
            "max_writes_per_cycle": settings.max_writes_per_cycle,
            "max_attempts": settings.max_attempts,
            "bot_marker": config.bot_marker,
        },
    }
