# Changelog

All notable changes to **WTD (What To Do)** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-19

The platform release: WTD grows from a local recursive TODO engine into a
**standalone autonomous AI agent fleet platform** for the whole SDLC.

### Added
- **Fleet subsystem** (`wtd/fleet/`): an autonomous mechanism that discovers
  work across a roster of GitHub repos, queues it as deduplicated work items
  ("todos"), schedules it onto agent roles, dispatches Claude-powered runs,
  applies guarded GitHub actions, and records everything in a run ledger.
  - Deterministic discovery scanners: unlabeled issues → triage, bug-labeled
    issues → analysis, open PRs → review, standing CI failures → diagnosis,
    missing/thin READMEs → docs, opt-in weekly article cadence.
  - **Seven built-in agent roles** (triage, bug-hunter, reviewer, janitor,
    doc-writer, contributor, author) with least-privilege action grants, plus
    user overrides via `agents/*.md` (frontmatter + prompt).
  - **Token-capacity load balancer**: daily budgets per provider lane
    (subscription lane first, API spillover), reservations, rate-limit
    cooldowns, USD cap on the API lane, graceful deferral when exhausted.
  - Pure **scheduler** with priority bands and per-repo round-robin fairness.
  - **Structured outcome contract**: agents reply in JSON; actions are
    validated against role grants with hard caps, path safety (no escape, no
    `.github/workflows/` writes), forced `wtd/` branch prefixes.
  - **The flywheel**: agents report `discovered` todos every run; validated,
    deduped, enqueued.
  - Loop guards + dedup markers (`<!-- wtd-fleet:<key> -->`) so the fleet
    never acts on or duplicates its own output.
  - `wtd fleet` CLI group: `status`, `agents`, `queue`, `discover`, `plan`,
    `run [--apply]`, `loop [--apply]`, `budget`, `init`.
  - REST endpoints: `GET /v1/fleet/{status,queue,runs}`,
    `POST /v1/fleet/{discover,run}` (API can narrow to dry-run, never
    escalate to writes).
  - `wtd.yml` fleet configuration (roster, roles, scan toggles, caps,
    budgets) layered over env config; `wtd.yml.example` + `wtd fleet init`.
- **Provider layer** (`wtd/providers/`) — everything wired Claude-first:
  - `claude-code` (default): headless Claude Code via `claude -p`,
    authenticated by `CLAUDE_CODE_OAUTH_TOKEN` or local login; subscription
    lane kept pure by scrubbing `ANTHROPIC_API_KEY` from its environment.
  - `anthropic` (fallback): official SDK, default model `claude-opus-5`,
    streaming, adaptive thinking, typed error chain, server-side refusal
    fallbacks on Opus 5 (`WTD_ANTHROPIC_REFUSAL_FALLBACKS` to disable).
  - Automatic failover with per-lane availability probes;
    `WTD_LLM_PROVIDER=auto` is the new default.
- **Deployment**: `Dockerfile` (both lanes preinstalled) and a default-OFF
  scheduled GitHub Actions harness (`.github/workflows/fleet-loop.yml`,
  gated on the `WTD_FLEET_ENABLED` repo variable, OAuth-first secrets).
- **Docs**: README rewritten around the platform; `docs/FLEET.md`
  architecture + ADR-lite decisions; `env.example` reorganized.
- **Tests**: 116 new tests (152 total) — balancer, scheduler, outcome
  validation, discovery (mock GitHub transport), dispatcher and orchestrator
  integration (fake provider + fake GitHub), provider router failover, role
  registry, dashboard widget. All offline.

### Changed
- **Default provider flipped from Ollama to the Claude chain** (`auto`).
  Ollama and OpenAI remain as explicit opt-ins (`WTD_LLM_PROVIDER=ollama|openai`)
  and `ollama` moved from core dependencies to an extra (`pip install "wtd[ollama]"`).
- `anthropic` SDK and `pyyaml` are now core dependencies; the dead
  `claude-3-opus-20240229` default model was replaced by `claude-opus-5`
  (configurable via `WTD_MODEL`).
- `WTDAgent` now routes through the provider chain with failover and no
  longer returns provider error strings as model output (errors degrade to
  heuristics and are logged).
- Secrets are read from conventional unprefixed env vars too:
  `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`/`GH_TOKEN`.
- Project metadata: repository URLs point at `bamr87/wtd`; version 0.2.0.
- `wtd/db/models.py` migrated to SQLAlchemy 2.0 typed declarative mappings
  (`Mapped` / `mapped_column`), so the repository layer reads real Python
  types (`str`, `datetime | None`) instead of `Column[...]`; nullability is
  now declared per column. Verified with a full save/read round-trip.
- The mypy baseline is cleared (30 errors → 0) and **CI now gates on
  type-checking** instead of running it advisory-only; `check_untyped_defs`
  is enabled since the tree is clean at that level too.

### Fixed
- `wtd routines review` crashed: the CLI called a method
  (`get_routines_needing_review`) that didn't exist on `RoutineManager`.
- `wtd fleet loop --interval 0`-style falsy-zero intervals no longer fall
  back to the 900s default (orchestrator loop interval handling).
- **`wtd dashboard` could never open**: `TodoTreeWidget` defined a helper
  named `_add_node`, shadowing Textual's `Tree._add_node` internal (called
  as `self._add_node(parent, label, data)` from `Tree.__init__`). The
  clashing two-argument override made constructing the widget raise
  `TypeError`. Renamed to `_add_todo_node`, with regression tests.
- `create_app()` used attribute annotations on a non-`self` object
  (`app.state.trees: dict[...] = {}`), which is not valid typing syntax.

## [0.1.1] - 2025-12-20

### Added
- **`tree.json` repo core memory**: a durable, comprehensive, growing record of TODO “DNA” (history, references, indexes) that is continuously updated and used as the source of truth.
- **Tree persistence layer** via `wtd/core/tree_store.py`:
  - Stable TODO identity across runs (scanned TODOs keyed by file/line/raw text + content)
  - Append-only-ish history: scan records + per-node event history
  - DNA indexes: files, tags, contexts, status counts
  - Portable storage (no local absolute paths written to `tree.json`)

### Changed
- **CLI** now loads/merges/saves `tree.json` on every run and builds the runtime `TodoTree` from the store.
- **Dashboard** persists status updates (execute/complete/cancel) back into `tree.json`.
- **API** persists session tree changes back into `tree.json` on `/scan`, `/execute`, `/spawn`, and `/complete`.
- `TodoTree.to_dict()` now uses Pydantic JSON mode for safe serialization.

## [0.1.0] - 2025-12-19

### Added
- Initial WTD release: scanner, recursive tree, CLI, dashboard, and API.


