import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wtd.api.app import create_app
from wtd.cli import app as cli_app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def temp_repo(tmp_path: Path) -> Path:
    """
    Create a minimal "repo" that WTD can treat as a root (pyproject present),
    with a few TODOs to scan.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name="wtd-e2e-fixture"',
                'version="0.0.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    (repo / "README.md").write_text(
        "\n".join(
            [
                "# Fixture Repo",
                "",
                "- [ ] Fix the thing",
                "- [ ] Write docs for the thing",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (repo / "src.py").write_text(
        "\n".join(
            [
                "# TODO: fix a bug in parsing",
                "def foo():",
                "    return 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return repo


def _read_tree_json(repo: Path) -> dict:
    tree_path = repo / "tree.json"
    assert tree_path.exists(), "tree.json should exist after a scan"
    return json.loads(tree_path.read_text(encoding="utf-8"))


def test_cli_scan_status_execute_e2e(runner: CliRunner, temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WTD_LLM_PROVIDER", "mock")

    # scan
    res = runner.invoke(cli_app, ["scan", str(temp_repo)])
    assert res.exit_code == 0, res.output
    data = _read_tree_json(temp_repo)
    assert data["scans"], "scan history should be recorded"
    assert data["nodes"], "nodes should be populated"

    # status (should build tree from tree.json)
    res = runner.invoke(cli_app, ["status", str(temp_repo)])
    assert res.exit_code == 0, res.output
    assert "TODO Tree" in res.output

    # execute one actionable todo (auto mode to avoid prompts)
    res = runner.invoke(cli_app, ["execute", str(temp_repo), "--auto"])
    assert res.exit_code == 0, res.output

    # ensure tree.json got a tree_applied event after execution
    data = _read_tree_json(temp_repo)
    events = [e.get("event") for e in data.get("events", [])]
    assert "tree_applied" in events, "execution should persist state back to tree.json"


def test_api_scan_execute_tree_e2e(temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WTD_LLM_PROVIDER", "mock")

    api = create_app()
    client = TestClient(api)

    scan = client.post("/v1/wtd/scan", json={"path": str(temp_repo)})
    assert scan.status_code == 200, scan.text
    payload = scan.json()
    assert payload["todo_count"] > 0
    session_id = payload["session_id"]

    # tree endpoint works
    tree = client.get(f"/v1/wtd/tree/{session_id}")
    assert tree.status_code == 200, tree.text
    tree_payload = tree.json()
    assert tree_payload["nodes"], "tree should include nodes"

    # execute endpoint persists state
    exe = client.post("/v1/wtd/execute", json={"session_id": session_id})
    assert exe.status_code == 200, exe.text

    data = _read_tree_json(temp_repo)
    events = [e.get("event") for e in data.get("events", [])]
    assert "scan_merged" in events
    assert "tree_applied" in events


