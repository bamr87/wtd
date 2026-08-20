"""Derive a fleet manifest by reading a repository.

Harmonization only works if adopting the shared vocabulary is cheap. Rather
than asking six repositories to hand-write a descriptor, this module reads
what is already there — the workflow files — and infers the manifest:
which loops call a model, how they are triggered, which ``*_ENABLED``
variable gates them, what credentials they consume, and whether they merge.

Inference is deliberately conservative and honest about its confidence:
anything it cannot establish from the file is left unset rather than
guessed, and the emitted manifest is stamped ``provenance: derived`` so a
reader knows it was machine-read, not declared by a maintainer.

Parsing is done with PyYAML where the file is well-formed, falling back to
regex over the raw text. GitHub Actions files routinely contain the key
``on:``, which YAML 1.1 loads as the boolean ``True`` — handled explicitly
below rather than silently losing every trigger.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wtd.fleet.textscan import executes, find_evidence

if TYPE_CHECKING:
    from wtd.fleet.github import GitHubClient
from wtd.fleet.manifest import (
    FleetManifest,
    Guardrails,
    Harness,
    Lane,
    LaneKind,
    Metering,
    TokenSpec,
    Trigger,
    TriggerKind,
)

# --- how a lane invokes a model -------------------------------------------
_HARNESS_PATTERNS: list[tuple[Harness, re.Pattern[str]]] = [
    (Harness.CLAUDE_CODE_ACTION, re.compile(r"anthropics/claude-code-action")),
    (Harness.WTD_FLEET, re.compile(r"\bwtd\s+fleet\b")),
    (
        Harness.CLAUDE_CLI,
        re.compile(r"(?<!\w)claude\s+-p\b|@anthropic-ai/claude-code|claude_args"),
    ),
    (
        Harness.ENGINE,
        re.compile(r"germinate\.mjs|gate\.mjs|agentic_validate\.py|dash-gen"),
    ),
]

# --- what the lane is for, inferred from its name/file ---------------------
_KIND_HINTS: list[tuple[LaneKind, re.Pattern[str]]] = [
    (LaneKind.ORCHESTRATOR, re.compile(r"fleet-?loop|orchestrat|dispatcher", re.I)),
    (LaneKind.REVIEW, re.compile(r"review|pr-gate|gatekeep", re.I)),
    (LaneKind.TRIAGE, re.compile(r"triage|issue-pipeline|issue-factory|remediat", re.I)),
    (LaneKind.FANOUT, re.compile(r"fanout|standardiz|propagat|seed", re.I)),
    (
        LaneKind.MAINTENANCE,
        re.compile(r"auto-?fix|auto-?update|doctor|pulse|reconcile|drift|watch|expiry", re.I),
    ),
    (
        LaneKind.CONTENT,
        re.compile(r"content|scout|wire|epic|germinate|grow|garden|lineage|write|blog", re.I),
    ),
    (LaneKind.ANALYSIS, re.compile(r"analytic|usage|explore|metric|report|audit", re.I)),
]

_MERGE_PATTERNS = re.compile(
    r"gh pr merge|pulls/\S+/merge|--auto\s+--merge|merge_pull_request|enable_pr_auto_merge"
)
_PR_PATTERNS = re.compile(r"gh pr create|create-pull-request|pulls\b|open_pr")
_PUSH_MAIN = re.compile(r"git push[^\n]*\b(origin\s+)?(main|master)\b")

_SWITCH_RE = re.compile(r"\b([A-Z][A-Z0-9_]*_ENABLED)\b")
_SECRET_RE = re.compile(r"secrets\.([A-Z][A-Z0-9_]*)")
_CRON_RE = re.compile(r"cron:\s*['\"]([^'\"]+)['\"]")
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.M)

_EVENT_KEYS = {
    "push", "pull_request", "pull_request_target", "issues", "issue_comment",
    "workflow_run", "workflow_call", "release", "repository_dispatch",
    "pull_request_review", "pull_request_review_comment", "discussion",
}


def _load_yaml(text: str) -> dict[str, Any] | None:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _on_block(data: dict[str, Any]) -> Any:
    """Return the workflow's trigger block.

    ``on`` is a YAML 1.1 boolean, so a parsed Actions file carries the
    trigger block under the key ``True`` — not the string ``"on"``.
    """
    for key in (True, "on", "On", "ON"):
        if key in data:
            return data[key]
    return None


def detect_harness(text: str) -> Harness:
    for harness, pattern in _HARNESS_PATTERNS:
        if pattern.search(text):
            return harness
    return Harness.NONE


#: A mention handler is gated on a human typing @claude; it is not an
#: autonomous loop and must not be audited as one. Match the GATING
#: EXPRESSION, not the bare string: fan-out workflows legitimately carry
#: "@claude" as payload while seeding a mention handler into other repos.
_MENTION_RE = re.compile(r"contains\s*\(\s*github\.event[^)]*@claude", re.I)


def detect_kind(filename: str, workflow_name: str, text: str = "") -> LaneKind:
    """Classify a lane. The body decides mention-vs-autonomous; the name
    decides everything else."""
    if _MENTION_RE.search(text) or re.match(r"^claude\.ya?ml$", filename, re.I):
        return LaneKind.MENTION
    hay = f"{filename} {workflow_name}"
    for kind, pattern in _KIND_HINTS:
        if pattern.search(hay):
            return kind
    return LaneKind.OTHER


def _triggers(data: dict[str, Any] | None, text: str) -> list[Trigger]:
    triggers: list[Trigger] = []
    crons = _CRON_RE.findall(text)
    for cron in crons:
        triggers.append(Trigger(kind=TriggerKind.SCHEDULE, cron=cron.strip()))

    on = _on_block(data) if data else None
    events: list[str] = []
    dispatch = False
    if isinstance(on, dict):
        for key in on:
            k = str(key)
            if k == "workflow_dispatch":
                dispatch = True
            elif k in _EVENT_KEYS:
                events.append(k)
    elif isinstance(on, list):
        for key in on:
            k = str(key)
            if k == "workflow_dispatch":
                dispatch = True
            elif k in _EVENT_KEYS:
                events.append(k)
    elif on is None:
        # Unparseable file: fall back to scanning the raw text.
        dispatch = "workflow_dispatch" in text
        events = sorted({e for e in _EVENT_KEYS if re.search(rf"^\s*{e}:", text, re.M)})

    if events:
        triggers.append(Trigger(kind=TriggerKind.EVENT, events=sorted(set(events))))
    if dispatch:
        triggers.append(Trigger(kind=TriggerKind.DISPATCH))
    return triggers


def _guardrails(text: str) -> Guardrails:
    """Infer a lane's guardrails from what it executes.

    Behaviour is read from command lines only — a prompt that forbids
    merging, or a grep that hunts for merge commands, must not be read as
    the lane merging (see wtd.fleet.textscan).
    """
    return Guardrails(
        never_merges=not executes(text, _MERGE_PATTERNS),
        opens_pull_requests=executes(text, _PR_PATTERNS),
        writes_directly_to_default_branch=executes(text, _PUSH_MAIN),
    )


def merge_evidence(text: str) -> list[str]:
    """The command lines that made a lane look like it merges."""
    return find_evidence(text, _MERGE_PATTERNS)


def lane_from_workflow(path: Path) -> Lane | None:
    """Build a Lane from one workflow file, or None if it uses no model."""
    return lane_from_text(path.name, path.read_text(encoding="utf-8", errors="replace"))


def lane_from_text(filename: str, text: str) -> Lane | None:
    """Build a Lane from workflow source, or None if it uses no model.

    Shared by the local-checkout and GitHub-API paths so both infer
    identically.
    """
    harness = detect_harness(text)
    if harness is Harness.NONE:
        return None

    stem = filename.rsplit(".", 1)[0]
    data = _load_yaml(text)
    name_match = _NAME_RE.search(text)
    workflow_name = (
        name_match.group(1).strip().strip("\"'") if name_match else stem
    )
    switches = sorted(set(_SWITCH_RE.findall(text)))
    secrets = sorted(set(_SECRET_RE.findall(text)))

    return Lane(
        id=stem,
        kind=detect_kind(filename, workflow_name, text),
        harness=harness,
        implementation=f".github/workflows/{filename}",
        description=workflow_name,
        triggers=_triggers(data, text),
        # A workflow may reference several switches; the gate is the one it
        # is named for when present, else the first mentioned.
        switch=_pick_switch(switches, stem),
        uses_tokens=secrets,
        guardrails=_guardrails(text),
    )


def _pick_switch(switches: list[str], stem: str) -> str | None:
    if not switches:
        return None
    stem_key = re.sub(r"[^a-z0-9]+", "", stem.lower())
    for switch in switches:
        if re.sub(r"[^a-z0-9]+", "", switch.lower()).startswith(stem_key[:8] or "\0"):
            return switch
    return switches[0]


def _token_specs(lanes: list[Lane]) -> list[TokenSpec]:
    """Roll per-lane secret usage up into the fleet token contract."""
    purposes = {
        "CLAUDE_CODE_OAUTH_TOKEN": (
            "Preferred Claude auth (house convention: OAuth first). "
            "Produced by `claude setup-token`."
        ),
        "ANTHROPIC_API_KEY": "Fallback Claude auth, used only when the OAuth token is absent.",
        "FLEET_TOKEN": "Cross-repo PAT: lets a lane see and act on other repositories.",
        "GITHUB_TOKEN": "Ambient Actions token; cannot write to other repositories.",
    }
    scopes = {"FLEET_TOKEN": "fleet", "CLAUDE_CODE_OAUTH_TOKEN": "fleet",
              "ANTHROPIC_API_KEY": "fleet"}
    used: dict[str, list[str]] = {}
    for lane in lanes:
        for token in lane.uses_tokens:
            used.setdefault(token, []).append(lane.id)

    specs = []
    for name, lane_ids in sorted(used.items()):
        specs.append(
            TokenSpec(
                name=name,
                scope=scopes.get(name, "repo"),
                required=name != "ANTHROPIC_API_KEY",
                purpose=purposes.get(name, ""),
                used_by=lane_ids,
            )
        )
    return specs


def _inventory(repo_root: Path) -> tuple[list[str], list[str]]:
    agents_dir = repo_root / ".claude" / "agents"
    skills_dir = repo_root / ".claude" / "skills"
    agents = (
        sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    )
    skills = (
        sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
        if skills_dir.is_dir()
        else []
    )
    return agents, skills


def derive_manifest(
    repo_root: Path, repo_slug: str, *, summary: str = ""
) -> FleetManifest:
    """Read a repository checkout and derive its fleet manifest."""
    workflows = repo_root / ".github" / "workflows"
    lanes: list[Lane] = []
    if workflows.is_dir():
        for path in sorted(workflows.glob("*.y*ml")):
            lane = lane_from_workflow(path)
            if lane is not None:
                lanes.append(lane)

    agents, skills = _inventory(repo_root)
    return FleetManifest(
        repo=repo_slug,
        summary=summary,
        provenance="derived",
        lanes=lanes,
        tokens=_token_specs(lanes),
        metering=Metering(),
        agents=agents,
        skills=skills,
    )


async def derive_manifest_from_github(
    client: "GitHubClient", repo_slug: str, *, summary: str = ""
) -> FleetManifest:
    """Derive a manifest for a repo we have not cloned.

    Reads ``.github/workflows/`` through the REST API, so the tool can map
    and audit the whole fleet — not just what happens to be checked out.
    Individual unreadable files are skipped rather than failing the repo.
    """
    from wtd.fleet.github import GitHubError

    lanes: list[Lane] = []
    try:
        entries = await client.list_dir(repo_slug, ".github/workflows")
    except GitHubError:
        entries = []

    for entry in entries:
        name = str(entry.get("name", ""))
        if not name.endswith((".yml", ".yaml")):
            continue
        try:
            text = await client.get_file(repo_slug, f".github/workflows/{name}")
        except GitHubError:
            continue
        if not text:
            continue
        lane = lane_from_text(name, text)
        if lane is not None:
            lanes.append(lane)

    lanes.sort(key=lambda item: item.id)
    return FleetManifest(
        repo=repo_slug,
        summary=summary,
        provenance="derived",
        lanes=lanes,
        tokens=_token_specs(lanes),
        metering=Metering(),
    )
