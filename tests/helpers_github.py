"""Test helper: a canned-response GitHub API served over httpx MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx

from wtd.fleet.github import GitHubClient


class FakeGitHub:
    """Route table + request recorder behind a real GitHubClient.

    Routes are ``(method, path) -> payload``; payload may be a dict/list
    (JSON 200), an int (bare status), or a tuple ``(status, payload)``.
    Unrouted paths 404. All requests (with parsed JSON bodies for writes)
    are recorded for assertions.
    """

    def __init__(self, token: str | None = "test-token"):
        self.routes: dict[tuple[str, str], Any] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.client = GitHubClient(
            token, transport=httpx.MockTransport(self._handle)
        )

    def route(self, method: str, path: str, payload: Any) -> "FakeGitHub":
        self.routes[(method.upper(), path)] = payload
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except json.JSONDecodeError:
                body = request.content
        self.requests.append((request.method, path, body))

        payload = self.routes.get((request.method, path))
        if payload is None:
            return httpx.Response(404, json={"message": f"no route for {path}"})
        if isinstance(payload, int):
            return httpx.Response(payload, json={})
        if isinstance(payload, tuple):
            status, data = payload
            return httpx.Response(status, json=data)
        return httpx.Response(
            201 if request.method in ("POST", "PUT") else 200, json=payload
        )

    def writes(self) -> list[tuple[str, str, Any]]:
        return [r for r in self.requests if r[0] in ("POST", "PUT", "PATCH", "DELETE")]


def issue(number: int, title: str, *, labels=(), user="alice", body="", comments=0,
          user_type="User") -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": name} for name in labels],
        "user": {"login": user, "type": user_type},
        "comments": comments,
        "html_url": f"https://github.com/o/r/issues/{number}",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-10T00:00:00Z",
    }


def pull(number: int, title: str, *, user="alice", draft=False, user_type="User") -> dict:
    return {
        "number": number,
        "title": title,
        "body": f"PR body {number}",
        "draft": draft,
        "user": {"login": user, "type": user_type},
        "base": {"ref": "main"},
        "head": {"ref": f"feat-{number}"},
        "html_url": f"https://github.com/o/r/pull/{number}",
        "created_at": "2026-08-01T00:00:00Z",
    }


def workflow_run(run_id: int, path: str, conclusion: str, *, name: str | None = None) -> dict:
    return {
        "id": run_id,
        "name": name or path,
        "path": path,
        "conclusion": conclusion,
        "event": "push",
        "run_number": run_id,
        "head_sha": "abc123def456",
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
    }
