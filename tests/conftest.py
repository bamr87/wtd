"""Shared pytest fixtures for the WTD test suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from wtd.config import reset_config


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make every test see a clean WTDConfig pointed at a temp directory.

    This prevents tests from creating ``~/.wtd`` on the developer's machine
    and from leaking state between tests.
    """
    config_dir = tmp_path / "wtd-home"
    monkeypatch.setenv("WTD_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("WTD_DB_PATH", str(config_dir / "wtd.db"))
    # Make sure we don't accidentally read a developer's .env file.
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """Create a small synthetic repository with a mix of TODO sources."""
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "module.py").write_text(
        "def foo():\n"
        "    # TODO: implement caching layer for this function\n"
        "    return 1\n"
        "\n"
        "def bar():\n"
        "    # FIXME: handle the empty-input edge case properly\n"
        "    return 2\n",
        encoding="utf-8",
    )

    (repo / "script.js").write_text(
        "// TODO: refactor this monstrosity into smaller helpers\nfunction x() { return 1; }\n",
        encoding="utf-8",
    )

    (repo / "NOTES.md").write_text(
        "# Notes\n\n"
        "- [ ] Write unit tests for the scanner\n"
        "- [x] Already-completed task that should be ignored\n"
        "- [ ] Document the configuration options\n"
        "\n"
        "```python\n"
        "# TODO: this is inside a code block and should be ignored\n"
        "```\n",
        encoding="utf-8",
    )

    # Files in skipped directories should not be scanned.
    skipped = repo / "node_modules"
    skipped.mkdir()
    (skipped / "ignored.js").write_text(
        "// TODO: this should not appear in scan results\n", encoding="utf-8"
    )

    # Unsupported extension should be skipped.
    (repo / "binary.bin").write_text(
        "# TODO: should be ignored due to extension\n", encoding="utf-8"
    )

    return repo
