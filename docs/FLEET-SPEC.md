# The Fleet Manifest Spec (`fleet/v1`)

One descriptor, published by every repository that runs autonomous AI loops, so a single tool can view, monitor, audit, configure, and deploy the whole fleet.

## Why this exists

Several repositories in this fleet independently grew the same thing: scheduled workflows that run Claude, propose changes, and open pull requests. They converged on the same *conventions* — OAuth-first authentication, default-OFF kill switches, agents that never merge — but expressed them in six different dialects:

| Repo | Where its fleet is described | Dialect |
|---|---|---|
| `bamr87/bamr87` | `_data/fleet.yml` | toolchain, schedule, tokens, variables |
| `bamr87/gitorio` | `.factory/config.yml` + `blueprint.json` | machines, belts, blueprints |
| `bamr87/lifehacker.dev` | `AUTOPILOT.md` + `docs/CICD.md` + `*_ENABLED` vars | lanes, findings, queue |
| `bamr87/irony-works` | `engine/seed.config.yml` | cadence, gate, harness, metering, guardrails |
| `bamr87/wtd` | `wtd.yml` | roster, roles, budgets |
| `year-of-ai/…` | workflow YAML only | — |

Six dialects means no cross-repo view: you cannot ask "which loops run tonight, and which of them can I stop?" without reading six codebases. The manifest is the common denominator — deliberately *descriptive*, not prescriptive. Each repo keeps its own engine; the manifest is how tooling sees them all at once.

Vocabulary is borrowed from the prior art rather than invented: `tokens` (name/scope/required/purpose/used_by) comes from the hub's `_data/fleet.yml`; `harness`, `guardrails`, and `metering` come from irony-works' `seed.config.yml`; the `*_ENABLED` switch discipline comes from lifehacker.dev, which enforces it most strictly.

## The file

`fleet.manifest.yml`, at the repository root.

```yaml
spec_version: fleet/v1
repo: owner/name
provenance: declared          # or "derived" when machine-read
summary: One line on what this repo's fleet does.

lanes:
  - id: germinate                     # stable; the workflow stem by convention
    kind: content                     # what it is FOR (see below)
    harness: claude-cli               # how it invokes a model
    implementation: .github/workflows/germinate.yml
    description: Weekly germination cycle
    triggers:
      - {kind: schedule, cron: "0 6 * * 1"}
      - {kind: dispatch}
    switch: GERMINATE_ENABLED         # null means UNGATED
    uses_tokens: [CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY]
    guardrails:
      never_merges: true
      opens_pull_requests: true
      writable_paths: [vault/nursery/, vault/compost/]
      max_writes_per_run: 5
    state_paths: [vault/nursery/]

tokens:
  - name: CLAUDE_CODE_OAUTH_TOKEN
    scope: fleet
    required: true
    purpose: Preferred Claude auth (house convention: OAuth first).
    used_by: [germinate]

metering:
  daily_token_budget: 1500000
  daily_usd_budget: 10

agents: [scout, scribe]
skills: [grow-irony-works]
```

### `kind` — what a lane is for

`content` · `triage` · `review` · `maintenance` · `analysis` · `orchestrator` · `fanout` · `mention` · `other`

`mention` is special: it means a human triggers the lane by typing `@claude`. A mention handler is **never** treated as autonomous, however it is scheduled, because a person is in the loop by construction.

### `harness` — how it invokes a model

`claude-code-action` · `claude-cli` · `wtd-fleet` · `engine` · `none`

### `switch` — the off switch

The `*_ENABLED` repository variable that gates the lane. `null` means the lane is ungated, which for a scheduled lane is a critical audit finding: a loop you cannot stop without editing and pushing a workflow is a loop you cannot stop during an incident.

### `guardrails` — the promises a lane makes

Defaults are the conservative reading: a lane may write, but must never merge. That is the one rule every repository in this fleet already states out loud.

## The tool

```bash
wtd fleet map      # every AI lane across every repo, one table
wtd fleet audit    # conformance against the shared conventions
wtd fleet adopt <path> [--write]   # derive a manifest from a repo's workflows
```

`map` and `audit` read committed manifests where they exist and fall back to deriving one from the repository's workflows, so a repo that has not adopted the spec still appears. Both work against local checkouts (`--path`) or over the GitHub API (the roster in `wtd.yml`), because the fleet is larger than what is checked out at any moment.

`adopt` exists so adoption is cheap: it reads `.github/workflows/`, infers each lane, and stamps the result `provenance: derived`. Review it — inference is a starting point, not the last word.

## The conventions, as rules

`audit` encodes what the repos already say in prose. Severity is about blast radius, not tidiness.

| Rule | Severity | What it catches |
|---|---|---|
| `switch-required` | critical | A scheduled AI loop with no `*_ENABLED` kill switch |
| `never-merge` | critical | A lane that merges its own pull requests |
| `pr-only` | critical | A lane that pushes straight to the default branch |
| `oauth-first` | warning | A lane authenticating with the metered API key only |
| `auth-fallback` | info | OAuth with no API-key fallback if the token expires |
| `cross-repo-token` | warning | A fan-out lane relying on the ambient `GITHUB_TOKEN`, which cannot write to other repos and whose pushes fire no CI |
| `metering` | warning | Autonomous lanes with no declared token/spend ceiling |
| `cadence-collision` | info | Lanes contending for runners on the same cron minute |

## Reading behaviour, not prose

A workflow file contains three things that look identical to a naive matcher:

```yaml
run: gh pr merge --squash --auto             # the lane merges
prompt: "**Never merge a pull request.**"    # the lane is FORBIDDEN to merge
run: grep -nE 'gh pr merge' diff.txt         # the lane DETECTS merges
```

Treating them alike produced three false "this agent merges its own PRs" findings out of four on this audit's first run — including one against a repository whose documentation correctly states that no tier ever merges. `wtd/fleet/textscan.py` narrows the haystack to lines that plausibly execute something, dropping comments, prose prohibitions, and detector patterns. The technique is borrowed from gitorio's Fleet Ops engine, which reads command lines through an equivalent comment-stripper for the same reason.

The bias is deliberate: prefer missing a real behaviour to inventing one, because a false accusation costs a maintainer more than a missed nit. Tool grants (`--allowedTools "Bash(gh pr merge:*)"`) still count — conferring a capability on an agent *is* conferring it.

## Adopting the spec

1. `wtd fleet adopt . --write` in the repository.
2. Read the generated file. Fill in what inference cannot know: `summary`, `writable_paths`, `state_paths`, `metering`, and the `purpose` of each token.
3. Set `provenance: declared`.
4. Commit it. The lane inventory now has an owner, and `wtd fleet audit` will hold the repo to the conventions it already claims to follow.
