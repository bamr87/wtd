"""Tests for ``wtd.core.scanner``."""

from __future__ import annotations

from pathlib import Path

import pytest

from wtd.core.models import TodoContext, TodoPriority, TodoStatus
from wtd.core.scanner import TodoScanner


@pytest.mark.asyncio
async def test_scan_finds_code_and_markdown_todos(synthetic_repo: Path) -> None:
    """The scanner should pick up TODOs from comments and markdown checkboxes."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    titles = [t.title for t in result.todos]
    assert any("caching layer" in t for t in titles), titles
    assert any("empty-input edge case" in t for t in titles), titles
    assert any("smaller helpers" in t for t in titles), titles
    assert any("unit tests for the scanner" in t for t in titles), titles
    assert any("configuration options" in t for t in titles), titles


@pytest.mark.asyncio
async def test_scan_skips_completed_markdown_tasks(synthetic_repo: Path) -> None:
    """Checked markdown tasks (`- [x]`) must be ignored."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    assert all("Already-completed task" not in t.title for t in result.todos), [
        t.title for t in result.todos
    ]


@pytest.mark.asyncio
async def test_scan_skips_node_modules_and_unsupported_extensions(
    synthetic_repo: Path,
) -> None:
    """SKIP_DIRS and the extension allowlist should keep noise out."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    assert all(
        "should not appear" not in t.title and "should be ignored" not in t.title
        for t in result.todos
    ), [t.title for t in result.todos]


@pytest.mark.asyncio
async def test_scan_skips_todos_inside_markdown_code_blocks(
    synthetic_repo: Path,
) -> None:
    """TODOs inside fenced code blocks in markdown files should be ignored."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    assert all("inside a code block" not in t.title for t in result.todos), [
        t.title for t in result.todos
    ]


@pytest.mark.asyncio
async def test_priority_inferred_from_tag(synthetic_repo: Path) -> None:
    """FIXME should map to HIGH priority and TODO to MEDIUM."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    by_title = {t.title: t for t in result.todos}
    fixme = next(
        (n for title, n in by_title.items() if "empty-input edge case" in title),
        None,
    )
    todo = next(
        (n for title, n in by_title.items() if "caching layer" in title),
        None,
    )

    assert fixme is not None and fixme.priority == TodoPriority.HIGH
    assert todo is not None and todo.priority == TodoPriority.MEDIUM


@pytest.mark.asyncio
async def test_context_inference_for_known_keywords(tmp_path: Path) -> None:
    """The scanner's keyword heuristic should pick obvious contexts."""
    src = tmp_path / "a.py"
    src.write_text(
        "# TODO: refactor the data access layer for clarity\n"
        "# TODO: write documentation for the public API\n"
        "# FIXME: bug crashes the import on empty file\n",
        encoding="utf-8",
    )
    scanner = TodoScanner(tmp_path)
    result = await scanner.scan()

    contexts = {t.context for t in result.todos}
    assert TodoContext.REFACTOR in contexts
    assert TodoContext.WRITE in contexts
    assert TodoContext.BUGFIX in contexts


@pytest.mark.asyncio
async def test_scan_result_metadata(synthetic_repo: Path) -> None:
    """Scan results should report duration and at least one source scanned."""
    scanner = TodoScanner(synthetic_repo)
    result = await scanner.scan()

    assert result.scan_duration_ms >= 0
    assert result.sources_scanned > 0
    assert all(t.status == TodoStatus.PENDING for t in result.todos)


@pytest.mark.asyncio
async def test_short_text_is_filtered(tmp_path: Path) -> None:
    """Very short TODO text is treated as a likely false positive."""
    src = tmp_path / "x.py"
    src.write_text("# TODO: hi\n", encoding="utf-8")
    scanner = TodoScanner(tmp_path)
    result = await scanner.scan()
    assert result.todos == []


@pytest.mark.asyncio
async def test_scan_handles_empty_directory(tmp_path: Path) -> None:
    """Scanning a directory with no files should return an empty result, not crash."""
    scanner = TodoScanner(tmp_path)
    result = await scanner.scan()
    assert result.todos == []
    assert result.context == TodoContext.UNKNOWN
    assert result.confidence == 0.0
