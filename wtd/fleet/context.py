"""Context builders: gather the evidence an agent needs for a work item.

One builder per work kind. Builders fetch bounded amounts of data (never
whole repositories), format it as plain text for the prompt, and label
everything as untrusted repository content so prompts stay
injection-aware.
"""

from __future__ import annotations

from wtd.fleet.github import GitHubClient, GitHubError, has_marker
from wtd.fleet.models import WorkItem, WorkKind

MAX_CONTEXT_CHARS = 60_000
_MAX_PATCH_CHARS = 3_000
_MAX_FILES_IN_REVIEW = 25
_MAX_COMMENTS = 10


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"


def _fence(label: str, content: str) -> str:
    return (
        f"<untrusted {label}>\n{content.strip()}\n</untrusted {label}>"
        if content and content.strip()
        else f"<untrusted {label}>(empty)</untrusted {label}>"
    )


class ContextBuilder:
    def __init__(self, client: GitHubClient, *, bot_marker: str = "wtd-fleet"):
        self.client = client
        self.bot_marker = bot_marker

    async def build(self, item: WorkItem) -> str:
        builders = {
            WorkKind.TRIAGE_ISSUE: self._issue_context,
            WorkKind.FIX_BUG: self._issue_context,
            WorkKind.REVIEW_PR: self._pr_context,
            WorkKind.INVESTIGATE_CI: self._ci_context,
            WorkKind.WRITE_DOCS: self._docs_context,
            WorkKind.IMPROVE_CODE: self._code_context,
            WorkKind.WRITE_ARTICLE: self._article_context,
            WorkKind.CUSTOM: self._custom_context,
        }
        builder = builders.get(item.kind, self._custom_context)
        context = await builder(item)
        return _clip(context, MAX_CONTEXT_CHARS)

    # ------------------------------------------------------------------
    async def _issue_context(self, item: WorkItem) -> str:
        number = item.evidence.get("number")
        parts = [
            f"Repository: {item.repo}",
            f"Issue #{number}: {item.title}",
            f"Author: {item.evidence.get('author', 'unknown')}",
            f"Labels: {', '.join(item.evidence.get('labels', [])) or '(none)'}",
            f"URL: {item.url or '(none)'}",
            _fence("issue body", str(item.evidence.get("body", ""))),
        ]
        if number:
            try:
                comments = await self.client.list_issue_comments(item.repo, int(number))
            except GitHubError:
                comments = []
            shown = 0
            for comment in comments:
                body = str(comment.get("body", ""))
                if has_marker(body, self.bot_marker):
                    continue  # our own earlier replies are not new evidence
                shown += 1
                if shown > _MAX_COMMENTS:
                    break
                author = (comment.get("user") or {}).get("login", "unknown")
                parts.append(_fence(f"comment by {author}", _clip(body, 2000)))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    async def _pr_context(self, item: WorkItem) -> str:
        number = item.evidence.get("number")
        parts = [
            f"Repository: {item.repo}",
            f"Pull request #{number}: {item.title}",
            f"Author: {item.evidence.get('author', 'unknown')}"
            + (" (bot)" if item.evidence.get("is_bot_author") else "")
            + (" (opened by this fleet)" if item.evidence.get("fleet_authored") else ""),
            f"Base ← Head: {item.evidence.get('base')} ← {item.evidence.get('head')}",
            f"Draft: {'yes' if item.evidence.get('draft') else 'no'}",
            f"URL: {item.url or '(none)'}",
            _fence("pr description", str(item.description or "")),
        ]
        # CI state on the head commit. A reviewer asked to recommend a merge
        # must see whether the change is green; without this the model would
        # be guessing at the one fact the merge gate cares most about.
        head_sha = str(item.evidence.get("head_sha") or "")
        if head_sha:
            ci = await self._ci_summary(item.repo, head_sha)
            parts.append(f"CI on head {head_sha[:8]}: {ci}")
        if number:
            try:
                files = await self.client.list_pull_files(item.repo, int(number))
            except GitHubError:
                files = []
            parts.append(f"Changed files: {len(files)}")
            for entry in files[:_MAX_FILES_IN_REVIEW]:
                header = (
                    f"{entry.get('filename')} "
                    f"(+{entry.get('additions', 0)}/-{entry.get('deletions', 0)}, "
                    f"{entry.get('status', 'modified')})"
                )
                patch = entry.get("patch")
                if patch:
                    parts.append(
                        _fence(f"patch {header}", _clip(str(patch), _MAX_PATCH_CHARS))
                    )
                else:
                    parts.append(f"- {header} (patch unavailable — likely binary or large)")
            if len(files) > _MAX_FILES_IN_REVIEW:
                parts.append(f"…and {len(files) - _MAX_FILES_IN_REVIEW} more files not shown.")
        return "\n\n".join(parts)

    async def _ci_summary(self, repo: str, sha: str) -> str:
        """One line describing every check and status on a commit."""
        from wtd.fleet.mergegate import summarize_ci

        try:
            check_runs = await self.client.list_check_runs(repo, sha)
        except GitHubError:
            check_runs = []
        try:
            combined = await self.client.get_combined_status(repo, sha)
        except GitHubError:
            combined = {}
        summary = summarize_ci(check_runs, combined)
        verdict = "GREEN" if summary.green else "NOT GREEN"
        return f"{verdict} — {summary.describe()}"

    # ------------------------------------------------------------------
    async def _ci_context(self, item: WorkItem) -> str:
        workflow_path = str(item.evidence.get("workflow_path", ""))
        parts = [
            f"Repository: {item.repo}",
            f"Failing workflow: {workflow_path}",
            f"Branch: {item.evidence.get('branch')}",
            f"Run: #{item.evidence.get('run_number')} "
            f"(event: {item.evidence.get('event')}, sha: {item.evidence.get('head_sha')})",
            f"URL: {item.url or '(none)'}",
        ]
        if workflow_path:
            content = await self.client.get_file(item.repo, workflow_path)
            if content:
                parts.append(
                    _fence(f"workflow file {workflow_path}", _clip(content, 12_000))
                )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    async def _repo_overview(self, item: WorkItem) -> list[str]:
        parts = [f"Repository: {item.repo}"]
        try:
            repo_data = await self.client.get_repo(item.repo)
            parts.append(
                f"Description: {repo_data.get('description') or '(none)'} | "
                f"Language: {repo_data.get('language') or 'unknown'} | "
                f"Default branch: {repo_data.get('default_branch', 'main')}"
            )
        except GitHubError:
            pass
        entries = await self.client.list_dir(item.repo)
        if entries:
            listing = "\n".join(
                f"- {e.get('name')} ({e.get('type')})" for e in entries[:60]
            )
            parts.append(f"Top-level contents:\n{listing}")
        for manifest in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
            content = await self.client.get_file(item.repo, manifest)
            if content:
                parts.append(_fence(f"manifest {manifest}", _clip(content, 4000)))
                break
        return parts

    async def _docs_context(self, item: WorkItem) -> str:
        parts = await self._repo_overview(item)
        readme = await self.client.get_readme(item.repo)
        if readme:
            parts.append(_fence("current README", _clip(readme, 8000)))
        else:
            parts.append("The repository has NO README.")
        parts.append(f"Task: {item.title}\n{item.description}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    async def _code_context(self, item: WorkItem) -> str:
        parts = [
            f"Repository: {item.repo}",
            f"Task: {item.title}",
            f"Origin: {item.evidence.get('file_path', 'unknown')}"
            f":{item.evidence.get('line_number', '?')}",
        ]
        excerpt = str(item.evidence.get("excerpt", ""))
        if excerpt:
            parts.append(
                _fence(
                    f"code around {item.evidence.get('file_path')}",
                    _clip(excerpt, 8000),
                )
            )
        else:
            path = str(item.evidence.get("file_path", ""))
            if path:
                content = await self.client.get_file(item.repo, path)
                if content:
                    parts.append(_fence(f"file {path}", _clip(content, 20_000)))
        parts.append(_fence("todo text", item.description))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    async def _article_context(self, item: WorkItem) -> str:
        parts = await self._repo_overview(item)
        readme = await self.client.get_readme(item.repo)
        if readme:
            parts.append(_fence("README", _clip(readme, 8000)))
        try:
            merged = await self.client.list_pulls(item.repo, state="closed", per_page=10)
            titles = [
                f"- #{p.get('number')}: {p.get('title')}"
                for p in merged
                if p.get("merged_at")
            ]
            if titles:
                parts.append("Recently merged pull requests:\n" + "\n".join(titles))
        except GitHubError:
            pass
        parts.append(f"Task: {item.title}\n{item.description}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    async def _custom_context(self, item: WorkItem) -> str:
        parts = [
            f"Repository: {item.repo}",
            f"Task: {item.title}",
            _fence("task description", item.description),
        ]
        if item.evidence:
            import json

            parts.append(
                _fence("evidence", _clip(json.dumps(item.evidence, indent=2), 4000))
            )
        return "\n\n".join(parts)
