"""
WTD FastAPI Application
"""

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from wtd import __version__
from wtd.config import get_config
from wtd.core.agent import WTDAgent
from wtd.core.models import (
    TodoContext,
)
from wtd.core.scanner import TodoScanner
from wtd.core.tree import TodoTree
from wtd.core.tree_store import TreeStore, find_repo_root


def create_app() -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(
        title="WTD API",
        description="WTD – What To Do: The Ultimate Recursive TODO Engine API",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware. By default the API is local-first and accepts no
    # cross-origin requests; users opt in via WTD_API_CORS_ORIGINS. Note
    # that allow_credentials with an "*" origin is invalid per the CORS
    # spec and was a security misconfiguration in earlier versions.
    cfg = get_config()
    if cfg.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.api_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # Global state (in production, use proper state management)
    app.state.trees: dict[str, TodoTree] = {}
    app.state.agents: dict[str, WTDAgent] = {}
    app.state.stores: dict[str, TreeStore] = {}

    # Request/Response models
    class ScanRequest(BaseModel):
        path: str = Field(default=".", description="Path to scan")

    class ScanResponse(BaseModel):
        context: str
        confidence: float
        todo_count: int
        todos: list[dict[str, Any]]
        session_id: str
        scan_duration_ms: int

    class ExecuteRequest(BaseModel):
        session_id: str
        todo_id: str | None = None
        auto_spawn: bool = True

    class ExecuteResponse(BaseModel):
        success: bool
        action: str
        output: str
        spawned_count: int
        duration_ms: int

    class DashboardResponse(BaseModel):
        layout: str | None
        tabs: list[str]
        terminals: list[dict[str, Any]]
        files: list[str]

    class TreeResponse(BaseModel):
        nodes: list[dict[str, Any]]
        progress: dict[str, Any]

    class SpawnRequest(BaseModel):
        session_id: str
        parent_id: str
        max_subtasks: int = 5

    class SpawnResponse(BaseModel):
        spawned: list[dict[str, Any]]
        count: int

    # Routes
    @app.get("/")
    async def root():
        """API root - health check."""
        return {
            "name": "WTD API",
            "version": __version__,
            "status": "running",
        }

    @app.get("/v1/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    @app.post("/v1/wtd/scan", response_model=ScanResponse)
    async def scan(request: ScanRequest):
        """
        Scan a directory for TODOs.

        Endpoint: POST /v1/wtd/scan
        Trigger: `wtd` command
        """
        scan_path = Path(request.path).resolve()

        if not scan_path.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {request.path}")

        # Scan
        scanner = TodoScanner(scan_path)
        result = await scanner.scan()

        # Create session
        import uuid

        session_id = str(uuid.uuid4())[:8]

        # Initialize agent and tree
        config = get_config()
        agent = WTDAgent(config)
        context = await agent.analyze_context(result)

        # Persist/merge into tree.json (repo source of truth)
        repo_root = find_repo_root(scan_path)
        store = TreeStore(repo_root).load()
        node_ids = store.merge_scan(result, scan_path=scan_path)
        store.save()

        tree = store.to_todotree(node_ids=node_ids, include_done=True)
        agent.tree = tree

        # Store in app state
        app.state.trees[session_id] = tree
        app.state.agents[session_id] = agent
        app.state.stores[session_id] = store

        return ScanResponse(
            context=context.value,
            confidence=result.confidence,
            todo_count=len(result.todos),
            todos=[
                {
                    "id": str(t.id),
                    "title": t.title,
                    "context": t.context.value,
                    "priority": t.priority.value,
                    "status": t.status.value,
                }
                for t in result.todos[:50]  # Limit response size
            ],
            session_id=session_id,
            scan_duration_ms=result.scan_duration_ms,
        )

    @app.post("/v1/wtd/execute", response_model=ExecuteResponse)
    async def execute(request: ExecuteRequest, background_tasks: BackgroundTasks):
        """
        Execute a TODO or the next actionable one.

        Endpoint: POST /v1/wtd/execute
        Trigger: Sub-task ready
        """
        tree = app.state.trees.get(request.session_id)
        agent = app.state.agents.get(request.session_id)
        store = app.state.stores.get(request.session_id)

        if not tree or not agent:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get todo to execute
        if request.todo_id:
            todo = tree.get_node(UUID(request.todo_id))
        else:
            todo = tree.get_next_actionable()

        if not todo:
            return ExecuteResponse(
                success=True,
                action="none",
                output="No actionable TODOs",
                spawned_count=0,
                duration_ms=0,
            )

        # Execute
        result = await agent.execute_todo(todo)

        # Persist tree changes
        if store is not None:
            store.apply_tree(agent.tree, source="api_execute")
            store.save()

        return ExecuteResponse(
            success=result.success,
            action=result.action_type,
            output=result.output,
            spawned_count=len(result.spawned_todos),
            duration_ms=result.duration_ms,
        )

    @app.get("/v1/wtd/dashboard/{session_id}", response_model=DashboardResponse)
    async def dashboard(session_id: str):
        """
        Get workspace dashboard configuration.

        Endpoint: GET /v1/wtd/dashboard
        Trigger: Context detected
        """
        tree = app.state.trees.get(session_id)
        agent = app.state.agents.get(session_id)

        if not tree or not agent:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get workspace config
        todos = tree.all_nodes
        if todos:
            context = todos[0].context
        else:
            context = TodoContext.UNKNOWN

        config = await agent.suggest_workspace(context, todos)

        return DashboardResponse(
            layout=str(config.vscode_workspace) if config.vscode_workspace else None,
            tabs=[str(f) for f in config.files_to_open],
            terminals=config.terminals,
            files=[str(f) for f in config.files_to_open],
        )

    @app.get("/v1/wtd/tree/{session_id}", response_model=TreeResponse)
    async def get_tree(session_id: str):
        """Get the current TODO tree state."""
        tree = app.state.trees.get(session_id)

        if not tree:
            raise HTTPException(status_code=404, detail="Session not found")

        return TreeResponse(
            nodes=[
                {
                    "id": str(n.id),
                    "parent_id": str(n.parent_id) if n.parent_id else None,
                    "title": n.title,
                    "status": n.status.value,
                    "context": n.context.value,
                    "priority": n.priority.value,
                    "depth": n.depth,
                    "fitness": n.fitness_score,
                    "children_count": len(n.children_ids),
                }
                for n in tree.all_nodes
            ],
            progress=tree.get_progress(),
        )

    @app.post("/v1/wtd/spawn", response_model=SpawnResponse)
    async def spawn_subtasks(request: SpawnRequest):
        """Spawn subtasks for a TODO."""
        tree = app.state.trees.get(request.session_id)
        agent = app.state.agents.get(request.session_id)
        store = app.state.stores.get(request.session_id)

        if not tree or not agent:
            raise HTTPException(status_code=404, detail="Session not found")

        todo = tree.get_node(UUID(request.parent_id))
        if not todo:
            raise HTTPException(status_code=404, detail="TODO not found")

        # Generate subtasks
        subtasks_data = await agent.generate_subtasks(todo, request.max_subtasks)

        # Spawn them
        spawned = tree.spawn_children(UUID(request.parent_id), subtasks_data)

        if store is not None:
            store.apply_tree(tree, source="api_spawn")
            store.save()

        return SpawnResponse(
            spawned=[
                {
                    "id": str(s.id),
                    "title": s.title,
                    "priority": s.priority.value,
                }
                for s in spawned
            ],
            count=len(spawned),
        )

    @app.post("/v1/wtd/complete/{session_id}/{todo_id}")
    async def complete_todo(session_id: str, todo_id: str):
        """Mark a TODO as complete."""
        tree = app.state.trees.get(session_id)
        store = app.state.stores.get(session_id)

        if not tree:
            raise HTTPException(status_code=404, detail="Session not found")

        success = tree.complete_node(UUID(todo_id))

        if not success:
            raise HTTPException(status_code=400, detail="Could not complete TODO")

        if store is not None:
            store.apply_tree(tree, source="api_complete")
            store.save()

        return {"success": True, "progress": tree.get_progress()}

    @app.delete("/v1/wtd/session/{session_id}")
    async def delete_session(session_id: str):
        """Delete a session."""
        if session_id in app.state.trees:
            del app.state.trees[session_id]
        if session_id in app.state.agents:
            del app.state.agents[session_id]

        return {"deleted": True}

    # ------------------------------------------------------------------
    # Fleet endpoints — monitor and drive the AI agent fleet
    # ------------------------------------------------------------------
    class FleetRunRequest(BaseModel):
        apply: bool = Field(
            default=False,
            description=(
                "Request write mode. Effective only when WTD_FLEET_APPLY=true; "
                "the API can narrow to dry-run but never widen to writes."
            ),
        )
        repos: list[str] | None = None
        roles: list[str] | None = None
        max_runs: int | None = Field(default=None, ge=1, le=50)
        skip_discovery: bool = False

    class FleetDiscoverRequest(BaseModel):
        repos: list[str] | None = None

    @app.get("/v1/fleet/status")
    async def fleet_status_endpoint():
        """Fleet health: providers, roster, queue, budgets, recent runs."""
        from wtd.fleet.monitor import fleet_status

        return fleet_status()

    @app.get("/v1/fleet/queue")
    async def fleet_queue_endpoint(include_done: bool = False):
        """The cross-repo work queue."""
        from wtd.fleet.models import WorkStatus
        from wtd.fleet.state import FleetState

        cfg = get_config()
        state = FleetState(cfg.fleet_state_path).load()
        items = list(state.items.values())
        if not include_done:
            items = [
                i
                for i in items
                if i.status
                in (WorkStatus.QUEUED, WorkStatus.DEFERRED, WorkStatus.SCHEDULED)
            ]
        items.sort(key=lambda i: i.created_at)
        return {
            "total": len(items),
            "items": [i.model_dump(mode="json") for i in items[:200]],
        }

    @app.get("/v1/fleet/runs")
    async def fleet_runs_endpoint(limit: int = 20):
        """Recent agent runs from the ledger."""
        from wtd.fleet.state import FleetState

        cfg = get_config()
        state = FleetState(cfg.fleet_state_path).load()
        return {
            "runs": [
                r.model_dump(mode="json")
                for r in state.recent_runs(min(max(limit, 1), 100))
            ]
        }

    @app.post("/v1/fleet/discover")
    async def fleet_discover_endpoint(request: FleetDiscoverRequest):
        """Run work discovery across the roster and enqueue new items."""
        from wtd.fleet.orchestrator import FleetOrchestrator

        orchestrator = FleetOrchestrator()
        try:
            new = await orchestrator.discover(request.repos)
            pending = orchestrator.state.pending(
                max_attempts=orchestrator.settings.max_attempts
            )
            return {"discovered_new": new, "queue_pending": len(pending)}
        finally:
            await orchestrator.aclose()

    @app.post("/v1/fleet/run")
    async def fleet_run_endpoint(request: FleetRunRequest):
        """Run one fleet cycle.

        Write mode requires BOTH the request flag and WTD_FLEET_APPLY=true —
        the unauthenticated local API must not be able to escalate a
        dry-run deployment into one that writes to GitHub.
        """
        from wtd.fleet.orchestrator import FleetOrchestrator

        cfg = get_config()
        effective_apply = request.apply and cfg.fleet_apply

        orchestrator = FleetOrchestrator()
        try:
            report = await orchestrator.cycle(
                apply=effective_apply,
                repos=request.repos,
                roles=request.roles,
                max_runs=request.max_runs,
                skip_discovery=request.skip_discovery,
            )
        finally:
            await orchestrator.aclose()

        return {
            "enabled": report.enabled,
            "apply": report.apply,
            "discovered_new": report.discovered_new,
            "scheduled": report.scheduled,
            "completed": report.completed,
            "failed": report.failed,
            "actions_applied": report.actions_applied,
            "queue_pending": report.queue_pending,
            "notes": report.notes,
            "runs": [r.model_dump(mode="json") for r in report.runs],
        }

    return app


# For direct running
if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8787)
