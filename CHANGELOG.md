# Changelog

All notable changes to **WTD (What To Do)** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **The daily harness** (`wtd fleet daily`) — a once-a-day pass over the whole
  roster, on top of the 4-hourly cycle. The per-cycle scanners ask "what is
  broken now"; this asks "what has gone stale since yesterday", and it is the
  only place a merge can happen:
  - `wtd/fleet/docsdrift.py` — pure docs-drift assessment. Four cheap reads per
    repo (newest commit, newest commit per documentation path, commits since,
    README size) decide whether the docs still describe the code. Drift needs
    *both* a time lag and a commit count, so a quiet repo is never nagged and a
    busy one that documents as it goes is left alone. A stale verdict becomes
    one `write_docs` todo per repo per UTC day — the date is in the dedup
    anchor, which is what makes the check daily rather than once-ever.
  - `wtd/fleet/daily.py` — the three sweeps (docs, review, merge) and their
    report. Reviews are keyed by `pr#<n>@<head sha>`: a push earns a fresh
    review, an untouched branch costs nothing.
  - `wtd/fleet/mergegate.py` — the pure merge decision. It collects *every*
    blocker instead of short-circuiting, so "why didn't this merge?" has a
    complete answer in one line. "CI green" means every check completed, none
    failed, and at least one signal exists — a commit nothing has checked is
    not green.
  - The `reviewer` role now runs on `claude-opus-5` (pinned on the role, not
    inherited from the platform default), sees the CI status of the head commit
    in its context, and may request the new `merge_pr` action.
  - `.github/workflows/fleet-daily.yml` (07:17 UTC, default-OFF), the
    `POST /v1/fleet/daily` endpoint, and `wtd fleet merge-check` — a read-only
    window into why the gate is holding each open pull request.
  - 41 new tests (263 total) covering the gate's refusals, the drift
    thresholds, sweep idempotence, and the dispatcher's merge path.
- Merging is **two-key and four-locked**: an Opus 5 reviewer *recommends*, pure
  code re-verified against live GitHub state *decides*, and the approval is
  pinned to the exact head commit the reviewer read (the merge call passes that
  SHA, so a race between verdict and merge fails server-side instead of merging
  unreviewed code). The locks are `--apply`, `fleet.merge.enabled`, the repo's
  own `merge: true`, and the gate itself. `allow_fleet_authored` defaults to
  false, so the fleet reviews but does not merge its own pull requests.

### Changed
- `fleet.manifest.yml` declares `never_merges: false` for the new `fleet-daily`
  lane, so `wtd fleet audit` now reports one critical `never-merge` finding
  against this repository. That is deliberate: the fleet's shared convention is
  that no lane merges its own work, this lane is the sanctioned exception, and
  an audit that hides its own deviation is worthless. The declaration has to be
  hand-written because the merge happens inside the installed `wtd` package —
  a scanner reading the workflow text sees no merge command and would infer the
  wrong thing.
- **Fleet harmonization layer** — a shared `fleet/v1` manifest so several
  repositories running their own autonomous AI loops can be viewed, audited,
  and operated through one tool:
  - `wtd/fleet/manifest.py` — the spec: lanes (kind/harness/triggers/switch),
    a token contract, guardrails, and metering. Vocabulary is adopted from the
    prior art in the fleet rather than invented (the hub's `tokens:` shape,
    irony-works' `harness`/`guardrails`/`metering`, lifehacker.dev's
    `*_ENABLED` switch discipline).
  - `wtd/fleet/adopt.py` — derives a manifest by reading a repo's workflows,
    from a local checkout or over the GitHub API, so adoption is generated
    rather than hand-written.
  - `wtd/fleet/conventions.py` — the house rules as executable checks
    (switch-required, never-merge, pr-only, oauth-first, cross-repo-token,
    metering, cadence-collision) producing a per-repo score and grade.
  - `wtd/fleet/textscan.py` — reads what a workflow *does*, not what it talks
    about. A prompt forbidding merges, a grep detecting them, and a real merge
    command all contain `gh pr merge`; conflating them produced three false
    findings out of four on the first audit run. Technique borrowed from
    gitorio's Fleet Ops comment-stripper.
  - CLI: `wtd fleet map | audit | adopt`, plus `fleet.manifest.yml` for wtd.
  - `docs/FLEET-SPEC.md` — the spec, the rule table, and the adoption steps.
- 52 new tests (204 total), including regression cases for each false-positive
  shape found against the real repositories.

### Fixed
- The roster's per-repo `roles:` list is now **enforced**. It was parsed and
  validated, documented as "which roles run where", and then read by nothing:
  `plan_cycle` resolved every item against the global registry, so a repo
  listing `roles: [triage]` still got the reviewer, the doc-writer, and every
  other enabled role dispatched against it — a cost and blast-radius surprise
  for an operator who reasonably read the field as "only these agents touch
  this repo". `plan_cycle` now narrows the registry per repository before
  resolving, and an item no permitted role handles is reported as unroutable
  rather than quietly handed to someone else. An empty or omitted list keeps
  the documented "every enabled role" default, so narrowing stays opt-in, and
  a name matching no loaded role narrows to nothing (with a warning) rather
  than failing open. The scheduler stays pure: the mapping is passed in.
- `GitHubClient.list_commits` passed its `path` filter through
  `get(path, **params)`, where it collided with that method's own positional
  argument — every path-filtered commit query raised `TypeError` instead of
  reaching GitHub. Found by the first docs-sweep test.
- Documentation drift corrected against the tree: the README advertised 148
  tests (the suite is 204) and both the README and `CLAUDE.md` still described
  `mypy` as advisory, though CI has gated on it since 0.2.0.
- `docs/FLEET.md` D5 now records that dedup is **two-layered** — the local
  queue is a cache, the GitHub marker comment is the durable record — which is
  what lets independent harnesses share a fleet without double-posting.
  Evidenced by the first unattended Actions cycle: a cold state cache
  rediscovered already-completed work, dispatched six agents, and the marker
  check refused all six writes.

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


