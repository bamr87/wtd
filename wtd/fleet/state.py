"""Persistent fleet state: the work queue and the run ledger.

Stored in the fleet state directory (default ``~/.wtd/fleet``):

* ``queue.json``  — every known :class:`WorkItem`, keyed by ``dedup_key``.
* ``runs.jsonl`` — append-only :class:`AgentRunRecord` ledger.

Writes are atomic (tmp file + rename) so a crashed cycle never corrupts
the queue.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from wtd.fleet.models import AgentRunRecord, WorkItem, WorkStatus, utcnow

_QUEUE_FILE = "queue.json"
_RUNS_FILE = "runs.jsonl"
_SCHEMA_VERSION = 1


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class FleetState:
    """Load/mutate/persist the fleet queue and ledger."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.items: dict[str, WorkItem] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @property
    def queue_path(self) -> Path:
        return self.state_dir / _QUEUE_FILE

    @property
    def runs_path(self) -> Path:
        return self.state_dir / _RUNS_FILE

    def load(self) -> "FleetState":
        self.items = {}
        if self.queue_path.is_file():
            try:
                raw = json.loads(self.queue_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
            for record in raw.get("items", []):
                try:
                    item = WorkItem(**record)
                except Exception:
                    continue  # drop unreadable records rather than crash the fleet
                self.items[item.dedup_key] = item
        self._loaded = True
        return self

    def save(self) -> None:
        payload = {
            "version": _SCHEMA_VERSION,
            "updated_at": utcnow().isoformat(),
            "items": [
                item.model_dump(mode="json")
                for item in sorted(self.items.values(), key=lambda i: i.created_at)
            ],
        }
        _atomic_write(self.queue_path, json.dumps(payload, indent=2))

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------
    def enqueue(self, item: WorkItem) -> bool:
        """Add a work item unless its dedup_key is already known.

        Returns True when the item was new. Existing items are refreshed
        (evidence/url/priority) but keep their status and history, so a
        rescan never resets progress.
        """
        existing = self.items.get(item.dedup_key)
        if existing is None:
            self.items[item.dedup_key] = item
            return True
        existing.evidence.update(item.evidence)
        if item.url:
            existing.url = item.url
        existing.priority = item.priority
        existing.touch()
        return False

    def get(self, dedup_key: str) -> WorkItem | None:
        return self.items.get(dedup_key)

    def get_by_id(self, item_id: str) -> WorkItem | None:
        for item in self.items.values():
            if item.id == item_id:
                return item
        return None

    def pending(self, *, max_attempts: int = 3) -> list[WorkItem]:
        """Items eligible for scheduling."""
        return [
            item
            for item in self.items.values()
            if item.status in (WorkStatus.QUEUED, WorkStatus.DEFERRED)
            and item.attempts < max_attempts
        ]

    def mark(self, item: WorkItem, status: WorkStatus, *, error: str | None = None) -> None:
        item.status = status
        item.last_error = error
        item.touch()

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items.values():
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        return counts

    def prune_done(self, keep: int = 500) -> int:
        """Drop the oldest terminal items beyond ``keep`` to bound the file."""
        terminal = [
            i
            for i in self.items.values()
            if i.status in (WorkStatus.DONE, WorkStatus.SKIPPED, WorkStatus.FAILED)
        ]
        terminal.sort(key=lambda i: i.updated_at)
        removed = 0
        for item in terminal[: max(0, len(terminal) - keep)]:
            del self.items[item.dedup_key]
            removed += 1
        return removed

    # ------------------------------------------------------------------
    # Run ledger
    # ------------------------------------------------------------------
    def record_run(self, run: AgentRunRecord) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(run.model_dump(mode="json")) + "\n")

    def recent_runs(self, limit: int = 20) -> list[AgentRunRecord]:
        if not self.runs_path.is_file():
            return []
        lines = self.runs_path.read_text(encoding="utf-8").strip().splitlines()
        runs: list[AgentRunRecord] = []
        for line in lines[-limit:]:
            try:
                runs.append(AgentRunRecord(**json.loads(line)))
            except Exception:
                continue
        runs.reverse()
        return runs
