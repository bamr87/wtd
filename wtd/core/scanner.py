"""
WTD TODO Scanner - Scans files, issues, comments for TODOs
"""

import asyncio
import re
import time
from pathlib import Path
from typing import AsyncGenerator

from wtd.core.models import (
    ScanResult,
    TodoContext,
    TodoNode,
    TodoPriority,
    TodoSource,
    TodoStatus,
)


class TodoScanner:
    """
    Scans various sources for TODO items.
    
    Supports:
    - File comments (TODO, FIXME, HACK, XXX)
    - Markdown task lists
    - GitHub issues (via API)
    - Plain text notes
    """

    # Regex patterns for TODO detection
    COMMENT_PATTERNS = [
        # Standard TODO comments
        r"(?:#|//|/\*|\*|--|<!--|%)\s*(TODO|FIXME|HACK|XXX|BUG|NOTE)[\s:]+(.+?)(?:\*/|-->)?$",
        # Python/Shell style
        r"#\s*(TODO|FIXME|HACK|XXX)[\s:]+(.+)$",
        # C-style single line
        r"//\s*(TODO|FIXME|HACK|XXX)[\s:]+(.+)$",
    ]

    # Markdown task list pattern
    MARKDOWN_TASK_PATTERN = r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$"

    # File extensions to scan
    SCAN_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java",
        ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
        ".php", ".sh", ".bash", ".zsh", ".fish", ".ps1",
        ".md", ".markdown", ".txt", ".rst", ".org",
        ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
        ".html", ".css", ".scss", ".sass", ".less",
        ".sql", ".graphql", ".proto",
        ".dockerfile", ".makefile", ".cmake",
    }

    # Directories to skip
    SKIP_DIRS = {
        ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv",
        "venv", "env", ".env", "build", "dist", "target", ".next",
        ".nuxt", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "vendor", "packages", ".idea", ".vscode",
    }

    def __init__(self, root_path: Path | None = None):
        self.root_path = root_path or Path.cwd()
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE | re.MULTILINE)
            for p in self.COMMENT_PATTERNS
        ]
        self._markdown_pattern = re.compile(self.MARKDOWN_TASK_PATTERN, re.MULTILINE)

    async def scan(self, path: Path | None = None) -> ScanResult:
        """
        Scan a directory or file for TODOs.
        
        Args:
            path: Path to scan (defaults to root_path)
            
        Returns:
            ScanResult with discovered TODOs
        """
        start_time = time.time()
        path = path or self.root_path
        todos: list[TodoNode] = []
        sources_scanned = 0

        if path.is_file():
            todos.extend(await self._scan_file(path))
            sources_scanned = 1
        else:
            async for todo in self._scan_directory(path):
                todos.append(todo)
            sources_scanned = await self._count_scannable_files(path)

        # Detect overall context from TODOs
        context = self._detect_context(todos)
        
        duration_ms = int((time.time() - start_time) * 1000)

        return ScanResult(
            todos=todos,
            context=context,
            confidence=self._calculate_confidence(todos),
            scan_duration_ms=duration_ms,
            sources_scanned=sources_scanned,
        )

    async def _scan_directory(self, directory: Path) -> AsyncGenerator[TodoNode, None]:
        """Recursively scan a directory for TODOs."""
        try:
            for item in directory.iterdir():
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    if item.name.lower() not in self.SKIP_DIRS:
                        async for todo in self._scan_directory(item):
                            yield todo
                elif item.is_file():
                    if item.suffix.lower() in self.SCAN_EXTENSIONS:
                        for todo in await self._scan_file(item):
                            yield todo
        except PermissionError:
            pass

    async def _scan_file(self, file_path: Path) -> list[TodoNode]:
        """Scan a single file for TODOs."""
        todos: list[TodoNode] = []
        
        try:
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, FileNotFoundError):
            return todos

        lines = content.split("\n")
        
        # Track if we're inside a code block (for markdown files)
        in_code_block = False
        is_markdown = file_path.suffix.lower() in {".md", ".markdown"}

        for line_num, line in enumerate(lines, start=1):
            # Handle markdown code blocks
            if is_markdown:
                if line.strip().startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    continue

            # Check comment patterns
            for pattern in self._compiled_patterns:
                match = pattern.search(line)
                if match:
                    tag = match.group(1).upper()
                    text = match.group(2).strip()
                    # Skip very short or likely false positives
                    if len(text) > 3:
                        todos.append(self._create_todo_from_comment(
                            file_path, line_num, tag, text, line
                        ))
                    break

            # Check markdown tasks
            if is_markdown and not in_code_block:
                match = self._markdown_pattern.match(line)
                if match:
                    completed = match.group(1).lower() == "x"
                    text = match.group(2).strip()
                    if not completed and len(text) > 3:  # Only add valid uncompleted tasks
                        todos.append(self._create_todo_from_markdown(
                            file_path, line_num, text, line
                        ))

        return todos

    def _create_todo_from_comment(
        self,
        file_path: Path,
        line_num: int,
        tag: str,
        text: str,
        raw_line: str,
    ) -> TodoNode:
        """Create a TodoNode from a code comment."""
        priority = self._tag_to_priority(tag)
        context = self._infer_context_from_text(text)
        
        return TodoNode(
            title=text[:100],  # Truncate long titles
            description=text,
            status=TodoStatus.PENDING,
            context=context,
            priority=priority,
            source=TodoSource(
                file_path=file_path,
                line_number=line_num,
                source_type="comment",
                raw_text=raw_line.strip(),
            ),
            tags=[tag.lower()],
        )

    def _create_todo_from_markdown(
        self,
        file_path: Path,
        line_num: int,
        text: str,
        raw_line: str,
    ) -> TodoNode:
        """Create a TodoNode from a markdown task list item."""
        context = self._infer_context_from_text(text)
        
        return TodoNode(
            title=text[:100],
            description=text,
            status=TodoStatus.PENDING,
            context=context,
            priority=TodoPriority.MEDIUM,
            source=TodoSource(
                file_path=file_path,
                line_number=line_num,
                source_type="markdown_task",
                raw_text=raw_line.strip(),
            ),
            tags=["markdown"],
        )

    def _tag_to_priority(self, tag: str) -> TodoPriority:
        """Convert a TODO tag to priority."""
        mapping = {
            "FIXME": TodoPriority.HIGH,
            "BUG": TodoPriority.CRITICAL,
            "HACK": TodoPriority.MEDIUM,
            "XXX": TodoPriority.HIGH,
            "TODO": TodoPriority.MEDIUM,
            "NOTE": TodoPriority.LOW,
        }
        return mapping.get(tag.upper(), TodoPriority.MEDIUM)

    def _infer_context_from_text(self, text: str) -> TodoContext:
        """Infer context from TODO text content."""
        text_lower = text.lower()
        
        keywords = {
            TodoContext.BUGFIX: ["fix", "bug", "error", "issue", "crash", "broken"],
            TodoContext.WRITE: ["write", "document", "article", "blog", "readme", "docs"],
            TodoContext.PLAN: ["plan", "design", "architecture", "roadmap", "strategy"],
            TodoContext.LEARN: ["learn", "study", "research", "investigate", "explore"],
            TodoContext.BUILD: ["build", "create", "implement", "add", "feature", "develop"],
            TodoContext.REFACTOR: ["refactor", "clean", "improve", "optimize", "reorganize"],
            TodoContext.TEST: ["test", "spec", "coverage", "unit", "integration", "e2e"],
            TodoContext.DEPLOY: ["deploy", "release", "publish", "ship", "launch"],
        }
        
        for context, kws in keywords.items():
            if any(kw in text_lower for kw in kws):
                return context
        
        return TodoContext.UNKNOWN

    def _detect_context(self, todos: list[TodoNode]) -> TodoContext:
        """Detect the dominant context from a list of TODOs."""
        if not todos:
            return TodoContext.UNKNOWN
        
        context_counts: dict[TodoContext, int] = {}
        for todo in todos:
            context_counts[todo.context] = context_counts.get(todo.context, 0) + 1
        
        # Find most common context (excluding UNKNOWN)
        filtered = {k: v for k, v in context_counts.items() if k != TodoContext.UNKNOWN}
        if not filtered:
            return TodoContext.UNKNOWN
        
        return max(filtered, key=lambda k: filtered[k])

    def _calculate_confidence(self, todos: list[TodoNode]) -> float:
        """Calculate confidence score for context detection."""
        if not todos:
            return 0.0
        
        known_contexts = sum(1 for t in todos if t.context != TodoContext.UNKNOWN)
        return known_contexts / len(todos)

    async def _count_scannable_files(self, directory: Path) -> int:
        """Count files that would be scanned."""
        count = 0
        try:
            for item in directory.rglob("*"):
                if item.is_file() and item.suffix.lower() in self.SCAN_EXTENSIONS:
                    count += 1
        except PermissionError:
            pass
        return count


class GitHubIssueScanner:
    """Scanner for GitHub issues (future implementation)."""

    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = token

    async def scan(self) -> list[TodoNode]:
        """Scan GitHub issues for TODOs."""
        # TODO: Implement GitHub API integration
        return []

