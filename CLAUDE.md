# CLAUDE.md

Guidance for AI coding agents (Claude Code, Copilot, Cursor) working in **wtd**.

## What this is

WTD is a **standalone autonomous AI agent fleet platform** (Python). It discovers SDLC work across a roster of GitHub repos (issues, PRs, CI failures, docs gaps), queues it as deduplicated todos, and dispatches Claude-powered agent roles to act — comment, label, file issues, open draft PRs, write docs and articles — inside hard guardrails. All model calls ride **Claude Code OAuth by default, Anthropic API as fallback**, load-balanced on daily token budgets. Read `docs/FLEET.md` before touching `wtd/fleet/` or `wtd/providers/`.

## Stack & commands

```bash
# install dependencies:
pip install -e ".[dev]"
# run the CLI:
wtd fleet status          # fleet health; `wtd fleet run` = one dry-run cycle
wtd serve                 # REST API on 127.0.0.1:8787
# run tests (all offline — fake GitHub + fake providers):
pytest
# lint / typecheck:
ruff check .
mypy wtd                  # advisory (not yet strict-clean)
```

## Architecture in seven lines

1. `wtd/providers/` — the model lanes: `claude-code` (headless `claude -p`, OAuth/subscription, default) → `anthropic` (SDK, `claude-opus-5`, fallback); router fails over on retryable errors; legacy ollama/openai are explicit opt-ins only.
2. `wtd/fleet/discovery.py` scans the roster (wtd.yml) into `WorkItem`s with **stable dedup keys** — rescans converge, never duplicate.
3. `wtd/fleet/scheduler.py` (pure) matches items to roles: priority bands + per-repo round-robin fairness; `balancer.py` (pure) picks the provider lane by daily token budget with reservations and cooldowns.
4. `wtd/fleet/dispatcher.py` runs one agent: bounded untrusted-fenced context → JSON **output contract** → `outcome.py` validates every action against the role's grants → apply-gated GitHub writes with dedup markers.
5. `wtd/fleet/orchestrator.py` is the autonomous mechanism: `cycle()` (discover → schedule → dispatch → persist) and `loop()` (the daemon). Dry-run is the default posture; `--apply`/`WTD_FLEET_APPLY=true` is the only write gate.
6. `wtd/fleet/manifest.py` + `adopt.py` + `conventions.py` are the **harmonization layer**: one `fleet/v1` descriptor every repo publishes, derived automatically from its workflows, audited against the fleet's shared conventions (`wtd fleet map|audit|adopt`). Read `docs/FLEET-SPEC.md`.
7. The classic recursive TODO engine (`wtd/core/`) remains the local substrate (`wtd scan`, tree.json).

## Golden rules

- **Dry-run stays the default.** Never make writes reachable without the explicit apply gate, and never let the REST API escalate beyond the env's apply setting.
- **Agents never hold credentials or tools.** The platform performs all GitHub writes after validation; don't move mutations into prompts.
- **`.github/workflows/` is unwritable by agents — no exceptions** (it would execute with the fleet's own secrets). The guard lives in `outcome.py::_safe_rel_path`.
- **Everything the fleet writes carries the marker** (`<!-- wtd-fleet:<dedup_key> -->`) and everything it reads checks for it — that's what keeps the loop from feeding itself.
- **Keep pure things pure.** balancer/scheduler/outcome/discovery item-building take no I/O and stay clock-injected; new logic there needs unit tests in the same style (see `tests/`).
- **Read behaviour, not prose.** Workflow scanning goes through `wtd/fleet/textscan.py`: a prompt forbidding merges and a grep detecting them both contain `gh pr merge`, and conflating them produced three false findings out of four. Prefer missing a behaviour to inventing one.
- **Verify, don't recall**: check current `claude` CLI flags and `anthropic` SDK params against their docs before changing provider code; model IDs and API shapes drift.

## Conventions

- Conventional Commits: `type(scope): description` (`feat`/`fix`/`docs`/`refactor`/`test`/`chore`/`ci`).
- Default branch is `main` — branch from it and open a PR; never push to it directly.
- README-First, README-Last: read the nearest `README.md` before changing a directory, and update it after.
- Markdown house rule: **one paragraph per line** (CI-enforced; fix with `python3 tools/unwrap-prose.py --write`).
- Don't suppress type errors (`as any`, `@ts-ignore`, `# type: ignore`) or leave empty exception handlers.

## Fleet context

This repo is one of ~40 managed by the [bamr87/bamr87 dash](https://github.com/bamr87/bamr87) (registry: `_data/projects.yml`; tiered baseline: `docs/STANDARDS.md`). It is vendored there as a git submodule: commit and push changes **here** first — the hub only bumps its pointer afterwards. Shared CI, release, schema, and agent kits are seeded from the hub's `templates/`; prefer adopting those over hand-rolling equivalents. (Meta: wtd is itself the kind of agent platform the hub orchestrates — the hub's conventions, OAuth-first auth, default-OFF kill switches, and dedup-marker discipline are deliberately mirrored here.)
