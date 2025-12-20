"""
WTD Tree Store (tree.json)

This module makes `tree.json` the durable, append-only-ish source of truth for a repo:
- Comprehensive and growing history (scans + per-node events)
- References / DNA indexes (files -> todos, tag counts, context counts)
- Current state view (nodes with last-known status/actions/results)

Design goals:
- Deterministic node identity for scanned TODOs (stable across runs)
- JSON-safe serialization (no Path/datetime objects in stored payload)
- Atomic writes to avoid corruption
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from wtd.core.models import ScanResult, TodoNode, TodoSource, TodoStatus
from wtd.core.tree import TodoTree


SCHEMA_VERSION = 1
WTD_UUID_NAMESPACE = uuid.UUID("2dfbf5a9-4c7f-4b84-83d6-5b2f73b6b3b8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_repo_root(start: Path) -> Path:
    """
    Best-effort repo root detection.
    - If inside a git repo, returns the directory containing `.git`
    - Else returns nearest directory containing `pyproject.toml` or `requirements.txt`
    - Else falls back to the provided directory (or its parent if a file)
    """
    p = start.resolve()
    if p.is_file():
        p = p.parent

    candidates = [p, *p.parents]
    for d in candidates:
        if (d / ".git").exists():
            return d
    for d in candidates:
        if (d / "pyproject.toml").exists() or (d / "requirements.txt").exists():
            return d
    return p


def _relpath(repo_root: Path, file_path: Path | None) -> str | None:
    if file_path is None:
        return None
    try:
        return str(file_path.resolve().relative_to(repo_root.resolve()))
    except Exception:
        # Fall back to absolute if we can't relativize (e.g. different drive)
        return str(file_path)


def _normalize_text(s: str) -> str:
    return " ".join((s or "").strip().split())


def _read_git_origin_url(repo_root: Path) -> str | None:
    """
    Best-effort read of `.git/config` to extract remote "origin" URL.
    Avoids shelling out to git, and keeps this module dependency-free.
    """
    cfg = repo_root / ".git" / "config"
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[remote ") and '"origin"' in line:
            in_origin = True
            continue
        if in_origin and line.startswith("["):
            in_origin = False
        if in_origin and line.lower().startswith("url"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


def compute_repo_fingerprint(repo_root: Path) -> str:
    """
    Compute a portable repo fingerprint.
    Prefers git remote origin URL when available; otherwise falls back to repo folder name.
    """
    origin = _read_git_origin_url(repo_root)
    basis = origin or repo_root.name
    return sha256(basis.encode("utf-8")).hexdigest()[:16]


def stable_scanned_node_id(repo_fingerprint: str, repo_root: Path, todo: TodoNode) -> str:
    """
    Create a stable ID for scanned TODOs based on their source reference + content.
    This is the backbone identity used by `tree.json`.
    """
    source = todo.source or TodoSource(source_type="unknown")
    key = {
        "kind": "scanned",
        "repo": repo_fingerprint,
        "source_type": source.source_type or "unknown",
        "file": _relpath(repo_root, source.file_path),
        "line": source.line_number,
        "raw": _normalize_text(source.raw_text or ""),
        "title": _normalize_text(todo.title),
        "desc": _normalize_text(todo.description),
    }
    digest = sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()
    return f"todo_scanned_{digest[:32]}"


def stable_generated_node_id(parent_node_id: str, title: str, description: str = "") -> str:
    """
    Create a stable ID for generated/spawned TODOs.
    These are not anchored to a file line, so we scope them to the parent + content + time.
    """
    key = {
        "kind": "generated",
        "parent": parent_node_id,
        "title": _normalize_text(title),
        "desc": _normalize_text(description),
        "created": _utc_now_iso(),
    }
    digest = sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()
    return f"todo_gen_{digest[:32]}"


def node_uuid(repo_fingerprint: str, node_id: str) -> uuid.UUID:
    """Deterministic UUID for internal `TodoTree` keys."""
    return uuid.uuid5(WTD_UUID_NAMESPACE, f"wtd:{repo_fingerprint}:{node_id}")


@dataclass(frozen=True)
class TreePaths:
    repo_root: Path
    tree_path: Path


class TreeStore:
    """
    Loads, merges, and writes `tree.json` for a given repo root.
    """

    def __init__(self, repo_root: Path, tree_path: Path | None = None):
        self.repo_root = repo_root.resolve()
        self.tree_path = (tree_path or (self.repo_root / "tree.json")).resolve()
        self._data: dict[str, Any] = {}

    # ---------------------------------------------------------------------
    # Load / Save
    # ---------------------------------------------------------------------
    def load(self) -> "TreeStore":
        if not self.tree_path.exists():
            self._data = self._new_store()
            return self

        try:
            raw = self.tree_path.read_text(encoding="utf-8")
            self._data = json.loads(raw) if raw.strip() else self._new_store()
        except Exception:
            # If corrupted, keep a minimal safe store; do not crash core flows
            self._data = self._new_store()
            self._append_event("store_corrupt_or_unreadable", {"path": str(self.tree_path)})
        self._ensure_shape()
        self._normalize_repo_metadata()
        return self

    def save(self) -> None:
        self._ensure_shape()
        self._normalize_repo_metadata()
        self._data["repo"]["last_updated_at"] = _utc_now_iso()

        tmp_path = self.tree_path.with_suffix(self.tree_path.suffix + ".tmp")
        payload = json.dumps(self._data, indent=2, sort_keys=True)
        tmp_path.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp_path, self.tree_path)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        self._ensure_shape()
        return self._data

    @property
    def repo_fingerprint(self) -> str:
        fp = self._data.get("repo", {}).get("fingerprint")
        if fp and fp != "bootstrap":
            return str(fp)
        # Lazily compute (portable across clones if origin URL is set)
        digest = compute_repo_fingerprint(self.repo_root)
        self._data["repo"]["fingerprint"] = digest
        return digest

    def merge_scan(self, scan_result: ScanResult, scan_path: Path) -> list[str]:
        """
        Merge a scan into the store:
        - Updates/creates nodes (stable identity)
        - Appends a scan record
        - Updates references/dna indexes
        Returns node_ids included in this scan.
        """
        self._ensure_shape()
        now = _utc_now_iso()
        scan_id = sha256(f"{now}:{scan_path}:{len(scan_result.todos)}".encode("utf-8")).hexdigest()[:12]

        node_ids: list[str] = []
        for todo in scan_result.todos:
            nid = stable_scanned_node_id(self.repo_fingerprint, self.repo_root, todo)
            node_ids.append(nid)
            self._upsert_node_from_todo(nid, todo, now, discovered_via="scan")

        self._data["state"]["current_scan_id"] = scan_id
        self._data["state"]["current_node_ids"] = node_ids
        self._data["scans"].append(
            {
                "scan_id": scan_id,
                "at": now,
                "path": self._rel_scan_path(scan_path),
                "repo_root": ".",
                "sources_scanned": scan_result.sources_scanned,
                "todo_count": len(scan_result.todos),
                "confidence": scan_result.confidence,
                "context": scan_result.context.value,
                "duration_ms": scan_result.scan_duration_ms,
                "node_ids": node_ids,
            }
        )

        self._rebuild_indexes(node_ids=node_ids, now=now)
        self._append_event("scan_merged", {"scan_id": scan_id, "todo_count": len(node_ids)})
        return node_ids

    def apply_tree(self, tree: TodoTree, source: str = "runtime") -> None:
        """
        Sync in-memory tree state (actions, status, timestamps, parent/children) back into store.
        This keeps `tree.json` always updated when the UI or agent changes the tree.
        """
        self._ensure_shape()
        now = _utc_now_iso()
        for n in tree.all_nodes:
            nid = n.metadata.get("wtd_node_id")
            if not nid:
                # Best-effort: treat as generated node scoped to repo
                nid = stable_generated_node_id(parent_node_id="root", title=n.title, description=n.description)
                n.metadata["wtd_node_id"] = nid

            node = self._data["nodes"].get(nid)
            if node is None:
                self._data["nodes"][nid] = self._new_node_record(nid, now)
                node = self._data["nodes"][nid]

            # Status transitions
            prev_status = node.get("status")
            new_status = n.status.value
            if prev_status != new_status:
                self._append_node_event(nid, "status_changed", {"from": prev_status, "to": new_status, "source": source})

            # Core fields
            node["title"] = n.title
            node["description"] = n.description
            node["status"] = new_status
            node["context"] = n.context.value
            node["priority"] = n.priority.value
            node["tags"] = list(n.tags or [])
            node["last_seen_at"] = now

            # Relationships (store uses node_ids, not UUIDs)
            parent_nid = None
            if n.parent_id:
                parent = tree.get_node(n.parent_id)
                if parent and parent.metadata.get("wtd_node_id"):
                    parent_nid = parent.metadata["wtd_node_id"]
            node["parent_node_id"] = parent_nid

            children_nids: list[str] = []
            for cid in n.children_ids:
                child = tree.get_node(cid)
                if child and child.metadata.get("wtd_node_id"):
                    children_nids.append(child.metadata["wtd_node_id"])
            node["children_node_ids"] = children_nids

            # Execution / results
            node["actions"] = list(n.actions or [])
            node["result"] = n.result
            node["error"] = n.error
            node["timeline"]["created_at"] = node["timeline"].get("created_at") or n.created_at.isoformat()
            node["timeline"]["started_at"] = n.started_at.isoformat() if n.started_at else node["timeline"].get("started_at")
            node["timeline"]["completed_at"] = n.completed_at.isoformat() if n.completed_at else node["timeline"].get("completed_at")

        self._append_event("tree_applied", {"source": source, "node_count": len(tree.all_nodes)})

    def to_todotree(self, node_ids: Iterable[str] | None = None, include_done: bool = True) -> TodoTree:
        """
        Build a `TodoTree` from stored nodes.
        - If node_ids is provided, restricts to that set (plus any descendants that are stored)
        - If include_done is False, filters out done nodes (completed/cancelled/collapsed)
        """
        self._ensure_shape()
        wanted = set(node_ids) if node_ids is not None else set(self._data["nodes"].keys())

        # Expand to include descendants referenced by wanted nodes
        queue = list(wanted)
        while queue:
            nid = queue.pop()
            node = self._data["nodes"].get(nid) or {}
            for child in node.get("children_node_ids", []) or []:
                if child not in wanted:
                    wanted.add(child)
                    queue.append(child)

        tree = TodoTree()

        # Create nodes first
        uuid_to_node: dict[uuid.UUID, TodoNode] = {}
        for nid in wanted:
            rec = self._data["nodes"].get(nid)
            if not rec:
                continue
            try:
                status = TodoStatus(rec.get("status", "pending"))
            except Exception:
                status = TodoStatus.PENDING
            if not include_done and status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED, TodoStatus.COLLAPSED):
                continue

            # Source
            src = rec.get("source") or {}
            file_rel = src.get("file_path")
            source = TodoSource(
                file_path=(self.repo_root / file_rel) if file_rel else None,
                line_number=src.get("line_number"),
                source_type=src.get("source_type", "unknown"),
                url=src.get("url"),
                raw_text=src.get("raw_text", "") or "",
            )

            # Deterministic UUID for TodoTree identity
            tid = node_uuid(self.repo_fingerprint, nid)

            todo = TodoNode(
                id=tid,
                parent_id=None,  # set in second pass
                title=rec.get("title", ""),
                description=rec.get("description", "") or "",
                status=status,
                context=rec.get("context", "unknown"),
                priority=rec.get("priority", "medium"),
                source=source if (source.file_path or source.raw_text) else None,
                depth=int(rec.get("depth", 0) or 0),
                fitness_score=float(rec.get("fitness_score", 1.0) or 1.0),
                children_ids=[],
                actions=list(rec.get("actions") or []),
                result=rec.get("result"),
                error=rec.get("error"),
                tags=list(rec.get("tags") or []),
                metadata={"wtd_node_id": nid},
            )
            uuid_to_node[tid] = todo
            # Insert without mutating fields (TodoTree.add_root resets depth/fitness)
            tree._nodes[todo.id] = todo  # noqa: SLF001

        # Second pass: relationships + roots
        # Rebuild roots based on stored parent relationships
        tree._root_ids = []  # noqa: SLF001
        for todo in list(uuid_to_node.values()):
            nid = todo.metadata.get("wtd_node_id")
            rec = self._data["nodes"].get(nid or "")
            if not rec:
                continue
            parent_nid = rec.get("parent_node_id")
            if parent_nid:
                parent_uuid = node_uuid(self.repo_fingerprint, parent_nid)
                parent = uuid_to_node.get(parent_uuid)
                if parent:
                    todo.parent_id = parent.id
                    todo.depth = parent.depth + 1
                    if todo.id not in parent.children_ids:
                        parent.children_ids.append(todo.id)
                    continue
            # Root
            todo.parent_id = None
            todo.depth = 0
            tree._root_ids.append(todo.id)  # noqa: SLF001

        return tree

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _new_store(self) -> dict[str, Any]:
        now = _utc_now_iso()
        return {
            "schema_version": SCHEMA_VERSION,
            "repo": {
                "root": ".",
                "fingerprint": compute_repo_fingerprint(self.repo_root),
                "created_at": now,
                "last_updated_at": now,
                "last_scan_at": None,
            },
            "state": {
                "current_scan_id": None,
                "current_node_ids": [],
            },
            "nodes": {},
            "scans": [],
            "events": [],
            "indexes": {
                "files": {},
                "tags": {},
                "contexts": {},
                "status_counts": {},
            },
        }

    def _ensure_shape(self) -> None:
        if not self._data:
            self._data = self._new_store()
        self._data.setdefault("schema_version", SCHEMA_VERSION)
        self._data.setdefault("repo", {}).setdefault("root", ".")
        self._data.setdefault("state", {}).setdefault("current_node_ids", [])
        self._data.setdefault("nodes", {})
        self._data.setdefault("scans", [])
        self._data.setdefault("events", [])
        self._data.setdefault("indexes", {})
        self._data["indexes"].setdefault("files", {})
        self._data["indexes"].setdefault("tags", {})
        self._data["indexes"].setdefault("contexts", {})
        self._data["indexes"].setdefault("status_counts", {})

    def _normalize_repo_metadata(self) -> None:
        """
        Make sure repo metadata reflects the actual repo root.
        This also upgrades the bootstrapped `tree.json` shipped in the repo.
        """
        now = _utc_now_iso()
        repo = self._data.setdefault("repo", {})
        # Keep repo root portable for commits/clones
        repo["root"] = "."

        fp = repo.get("fingerprint")
        if not fp or fp == "bootstrap":
            repo["fingerprint"] = compute_repo_fingerprint(self.repo_root)

        # If the file was bootstrapped with 1970 timestamps, set created_at on first real load
        created_at = repo.get("created_at")
        if not created_at or str(created_at).startswith("1970-01-01"):
            repo["created_at"] = now
        # last_updated_at is always refreshed on save()

        # Sanitize existing scan records (older versions may have absolute paths)
        for scan in self._data.get("scans", []) or []:
            scan["repo_root"] = "."
            if "path" in scan and scan["path"]:
                scan["path"] = self._rel_scan_path(Path(str(scan["path"])))

    def _rel_scan_path(self, scan_path: Path) -> str:
        """Return scan path relative to repo root when possible."""
        try:
            p = scan_path.resolve()
            rr = self.repo_root.resolve()
            if p == rr:
                return "."
            if p.is_relative_to(rr):  # py311+
                return str(p.relative_to(rr))
        except Exception:
            pass
        return str(scan_path)

    def _new_node_record(self, node_id: str, now: str) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "kind": "unknown",
            "title": "",
            "description": "",
            "status": TodoStatus.PENDING.value,
            "context": "unknown",
            "priority": "medium",
            "tags": [],
            "metadata": {},
            "source": {},
            "parent_node_id": None,
            "children_node_ids": [],
            "actions": [],
            "result": None,
            "error": None,
            "depth": 0,
            "fitness_score": 1.0,
            "first_seen_at": now,
            "last_seen_at": now,
            "seen_count": 0,
            "timeline": {"created_at": None, "started_at": None, "completed_at": None},
            "history": [],
        }

    def _upsert_node_from_todo(self, node_id: str, todo: TodoNode, now: str, discovered_via: str) -> None:
        nodes = self._data["nodes"]
        rec = nodes.get(node_id)
        if rec is None:
            rec = self._new_node_record(node_id, now)
            rec["kind"] = "scanned"
            nodes[node_id] = rec
            self._append_node_event(node_id, "created", {"via": discovered_via})

        rec["seen_count"] = int(rec.get("seen_count", 0) or 0) + 1
        rec["last_seen_at"] = now
        rec["title"] = todo.title
        rec["description"] = todo.description
        rec["context"] = getattr(todo.context, "value", str(todo.context))
        rec["priority"] = getattr(todo.priority, "value", str(todo.priority))
        rec.setdefault("tags", [])
        rec["tags"] = list(todo.tags or rec["tags"])
        rec["depth"] = int(todo.depth or rec.get("depth", 0) or 0)
        rec["fitness_score"] = float(todo.fitness_score or rec.get("fitness_score", 1.0) or 1.0)

        # Preserve last-known status if it was advanced by execution/UI
        if rec.get("status") in (TodoStatus.COMPLETED.value, TodoStatus.CANCELLED.value, TodoStatus.COLLAPSED.value):
            pass
        else:
            rec["status"] = todo.status.value

        # Source
        if todo.source:
            rec["source"] = {
                "file_path": _relpath(self.repo_root, todo.source.file_path),
                "line_number": todo.source.line_number,
                "source_type": todo.source.source_type,
                "url": todo.source.url,
                "raw_text": todo.source.raw_text,
            }

        self._append_node_event(node_id, "seen", {"via": discovered_via})

    def _append_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        self._data["events"].append({"at": _utc_now_iso(), "event": event, "data": data or {}})

    def _append_node_event(self, node_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        rec = self._data["nodes"].get(node_id)
        if rec is None:
            return
        rec.setdefault("history", [])
        rec["history"].append({"at": _utc_now_iso(), "event": event, "data": data or {}})

    def _rebuild_indexes(self, node_ids: list[str], now: str) -> None:
        files_idx: dict[str, Any] = {}
        tags_idx: dict[str, int] = {}
        ctx_idx: dict[str, int] = {}
        status_counts: dict[str, int] = {}

        for nid in node_ids:
            rec = self._data["nodes"].get(nid) or {}
            status = rec.get("status", "pending")
            status_counts[status] = status_counts.get(status, 0) + 1

            ctx = rec.get("context", "unknown")
            ctx_idx[ctx] = ctx_idx.get(ctx, 0) + 1

            for tag in rec.get("tags", []) or []:
                tags_idx[tag] = tags_idx.get(tag, 0) + 1

            src = rec.get("source") or {}
            file_path = src.get("file_path")
            if file_path:
                fe = files_idx.get(file_path) or {"todo_ids": [], "todo_count": 0, "last_seen_at": now}
                fe["last_seen_at"] = now
                if nid not in fe["todo_ids"]:
                    fe["todo_ids"].append(nid)
                fe["todo_count"] = len(fe["todo_ids"])
                files_idx[file_path] = fe

        self._data["indexes"]["files"] = files_idx
        self._data["indexes"]["tags"] = tags_idx
        self._data["indexes"]["contexts"] = ctx_idx
        self._data["indexes"]["status_counts"] = status_counts
        self._data["repo"]["last_scan_at"] = now


