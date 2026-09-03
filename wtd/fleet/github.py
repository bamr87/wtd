"""Minimal async GitHub REST client for the fleet.

Deliberately small: only the endpoints discovery and the dispatcher need.
Reads work unauthenticated against public repos (rate-limited); every
write requires a token and is only reachable through the dispatcher's
apply gate.

The client accepts an ``httpx`` transport override so tests can run the
full request path against canned responses without any network.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

_USER_AGENT = "wtd-fleet"


class GitHubError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"GitHub API {status_code}: {message}")
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        api_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.authenticated = bool(token)
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        self._login: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method, path, params=params, json=json_body
            )
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"network error: {exc}") from exc
        if response.status_code == 404:
            raise GitHubError(404, f"not found: {path}")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message", ""))
            except Exception:
                detail = response.text[:200]
            raise GitHubError(response.status_code, f"{path}: {detail}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=params or None)

    async def post(self, path: str, **json_body: Any) -> Any:
        return await self._request("POST", path, json_body=json_body)

    async def put(self, path: str, **json_body: Any) -> Any:
        return await self._request("PUT", path, json_body=json_body)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    async def viewer_login(self) -> str | None:
        """The authenticated login, or None when unauthenticated."""
        if not self.authenticated:
            return None
        if self._login is None:
            data = await self.get("/user")
            self._login = str(data.get("login", "")) or None
        return self._login

    # ------------------------------------------------------------------
    # Reads used by discovery
    # ------------------------------------------------------------------
    async def get_repo(self, repo: str) -> dict[str, Any]:
        return await self.get(f"/repos/{repo}")

    async def list_issues(
        self, repo: str, *, state: str = "open", per_page: int = 30
    ) -> list[dict[str, Any]]:
        """Open issues, excluding pull requests."""
        data = await self.get(
            f"/repos/{repo}/issues",
            state=state,
            per_page=per_page,
            sort="updated",
            direction="desc",
        )
        return [item for item in data if "pull_request" not in item]

    async def list_pulls(
        self, repo: str, *, state: str = "open", per_page: int = 20
    ) -> list[dict[str, Any]]:
        return await self.get(
            f"/repos/{repo}/pulls",
            state=state,
            per_page=per_page,
            sort="updated",
            direction="desc",
        )

    async def list_pull_files(
        self, repo: str, number: int, *, per_page: int = 50
    ) -> list[dict[str, Any]]:
        return await self.get(f"/repos/{repo}/pulls/{number}/files", per_page=per_page)

    async def get_pull(self, repo: str, number: int) -> dict[str, Any]:
        """One pull request, including ``mergeable``/``mergeable_state``.

        Those two fields are absent from the list endpoint and computed
        lazily by GitHub, so a freshly pushed PR reports ``None`` until the
        background job finishes — the merge gate treats that as "retry",
        never as "fine".
        """
        return await self.get(f"/repos/{repo}/pulls/{number}")

    async def list_pull_reviews(
        self, repo: str, number: int, *, per_page: int = 50
    ) -> list[dict[str, Any]]:
        return await self.get(f"/repos/{repo}/pulls/{number}/reviews", per_page=per_page)

    async def list_check_runs(
        self, repo: str, ref: str, *, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """Check runs for a commit (the modern CI signal)."""
        data = await self.get(f"/repos/{repo}/commits/{ref}/check-runs", per_page=per_page)
        return (data or {}).get("check_runs", [])

    async def get_combined_status(self, repo: str, ref: str) -> dict[str, Any]:
        """The legacy commit-status rollup, still used by many integrations."""
        return await self.get(f"/repos/{repo}/commits/{ref}/status")

    async def list_commits(
        self,
        repo: str,
        *,
        path: str | None = None,
        since: str | None = None,
        sha: str | None = None,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """Commits on the default (or given) branch, optionally path-filtered."""
        params: dict[str, Any] = {"per_page": per_page}
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        if sha:
            params["sha"] = sha
        # Via _request, not get(): a "path" query parameter would collide
        # with get()'s own positional `path` argument.
        data = await self._request("GET", f"/repos/{repo}/commits", params=params)
        return data if isinstance(data, list) else []

    async def merge_inputs(self, repo: str, number: int) -> dict[str, Any]:
        """Everything the merge gate needs about one PR, in one call site.

        Fetched together so the decision describes a single moment: the
        pull request, the CI signals on its head commit, and its reviews.
        """
        pull = await self.get_pull(repo, number)
        head_sha = str((pull.get("head") or {}).get("sha", ""))
        check_runs: list[dict[str, Any]] = []
        combined: dict[str, Any] = {}
        if head_sha:
            try:
                check_runs = await self.list_check_runs(repo, head_sha)
            except GitHubError:
                check_runs = []
            try:
                combined = await self.get_combined_status(repo, head_sha)
            except GitHubError:
                combined = {}
        try:
            reviews = await self.list_pull_reviews(repo, number)
        except GitHubError:
            reviews = []
        return {
            "pull": pull,
            "head_sha": head_sha,
            "check_runs": check_runs,
            "combined_status": combined,
            "reviews": reviews,
        }

    async def list_issue_comments(
        self, repo: str, number: int, *, per_page: int = 50
    ) -> list[dict[str, Any]]:
        return await self.get(
            f"/repos/{repo}/issues/{number}/comments", per_page=per_page
        )

    async def list_workflow_runs(
        self, repo: str, *, branch: str | None = None, per_page: int = 20
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        data = await self.get(f"/repos/{repo}/actions/runs", **params)
        return data.get("workflow_runs", [])

    async def get_readme(self, repo: str) -> str | None:
        """Decoded README content, or None when the repo has none."""
        try:
            data = await self.get(f"/repos/{repo}/readme")
        except GitHubError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _decode_content(data)

    async def get_file(self, repo: str, path: str, *, ref: str | None = None) -> str | None:
        try:
            params = {"ref": ref} if ref else {}
            data = await self.get(f"/repos/{repo}/contents/{path}", **params)
        except GitHubError as exc:
            if exc.status_code == 404:
                return None
            raise
        if isinstance(data, list):  # a directory
            return None
        return _decode_content(data)

    async def list_dir(self, repo: str, path: str = "") -> list[dict[str, Any]]:
        try:
            data = await self.get(f"/repos/{repo}/contents/{path}".rstrip("/"))
        except GitHubError as exc:
            if exc.status_code == 404:
                return []
            raise
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Writes used by the dispatcher (apply mode only)
    # ------------------------------------------------------------------
    def _require_auth(self) -> None:
        if not self.authenticated:
            raise GitHubError(401, "a GitHub token is required for write actions")

    async def create_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        self._require_auth()
        return await self.post(f"/repos/{repo}/issues/{number}/comments", body=body)

    async def add_labels(self, repo: str, number: int, labels: list[str]) -> Any:
        self._require_auth()
        return await self.post(f"/repos/{repo}/issues/{number}/labels", labels=labels)

    async def create_issue(
        self, repo: str, title: str, body: str, *, labels: list[str] | None = None
    ) -> dict[str, Any]:
        self._require_auth()
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return await self._request("POST", f"/repos/{repo}/issues", json_body=payload)

    async def get_branch_sha(self, repo: str, branch: str) -> str:
        data = await self.get(f"/repos/{repo}/git/ref/heads/{branch}")
        return str(data["object"]["sha"])

    async def create_branch(self, repo: str, branch: str, from_sha: str) -> Any:
        self._require_auth()
        return await self.post(
            f"/repos/{repo}/git/refs", ref=f"refs/heads/{branch}", sha=from_sha
        )

    async def put_file(
        self,
        repo: str,
        path: str,
        content: str,
        *,
        branch: str,
        message: str,
    ) -> Any:
        self._require_auth()
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        # Updating an existing file requires its blob sha.
        try:
            existing = await self.get(f"/repos/{repo}/contents/{path}", ref=branch)
            if isinstance(existing, dict) and existing.get("sha"):
                payload["sha"] = existing["sha"]
        except GitHubError as exc:
            if exc.status_code != 404:
                raise
        return await self._request(
            "PUT", f"/repos/{repo}/contents/{path}", json_body=payload
        )

    async def create_pull(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, Any]:
        self._require_auth()
        return await self.post(
            f"/repos/{repo}/pulls",
            title=title,
            body=body,
            head=head,
            base=base,
            draft=draft,
        )

    async def merge_pull(
        self,
        repo: str,
        number: int,
        *,
        sha: str,
        method: str = "squash",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request, but only if its head is still ``sha``.

        Passing ``sha`` makes the merge conditional server-side: if anyone
        pushed between the gate's verdict and this call, GitHub refuses
        with 409 rather than merging code nothing reviewed.
        """
        self._require_auth()
        payload: dict[str, Any] = {"sha": sha, "merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message
        return await self._request(
            "PUT", f"/repos/{repo}/pulls/{number}/merge", json_body=payload
        )

    async def propose_pr(
        self,
        repo: str,
        *,
        branch: str,
        base: str,
        files: dict[str, str],
        title: str,
        body: str,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Create branch → commit files → open a draft PR."""
        base_sha = await self.get_branch_sha(repo, base)
        await self.create_branch(repo, branch, base_sha)
        for path, content in files.items():
            await self.put_file(
                repo,
                path,
                content,
                branch=branch,
                message=commit_message or f"{title} ({path})",
            )
        return await self.create_pull(
            repo, title=title, body=body, head=branch, base=base, draft=True
        )


def _decode_content(data: dict[str, Any]) -> str | None:
    content = data.get("content")
    if not content:
        return None
    if data.get("encoding") == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(content)


def marker_comment(bot_marker: str, dedup_key: str) -> str:
    """The invisible marker embedded in every fleet-authored body."""
    return f"<!-- {bot_marker}:{dedup_key} -->"


def has_marker(text: str, bot_marker: str, dedup_key: str | None = None) -> bool:
    """True when ``text`` was authored by the fleet (optionally for a key)."""
    if dedup_key is not None:
        return marker_comment(bot_marker, dedup_key) in (text or "")
    return f"<!-- {bot_marker}:" in (text or "")
