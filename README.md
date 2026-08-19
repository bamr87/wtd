# 🚀 WTD – What To Do

> **The autonomous AI agent fleet platform**

WTD turns every TODO — a code comment, an unlabeled GitHub issue, an unreviewed pull request, a failing workflow, a missing README — into queued work for a fleet of Claude-powered agents that **look for work, do the work, and add new todos** as they go.

```
╦ ╦╔╦╗╔╦╗
║║║ ║  ║║
╚╩╝ ╩ ═╩╝
```

It is a **standalone application**: a CLI, a daemon loop, and a REST API with local JSON state — no server infrastructure required. Everything is wired through **Claude Code OAuth by default** (your Claude subscription) with the **Anthropic API as fallback**, and a token-capacity load balancer decides which lane serves each agent run.

## ✨ What the fleet does

| Agent role | SDLC work it handles | Actions it may take |
|---|---|---|
| **triage** | new/unlabeled issues | comment, label |
| **bug-hunter** | bug-labeled issues, latent bugs | analysis comment, file issue |
| **reviewer** | open pull requests | review comment |
| **janitor** | standing CI failures | diagnosis issue, comment |
| **doc-writer** | missing or thin documentation | draft PR |
| **contributor** | TODO/FIXME debt in code | small draft PR |
| **author** | articles / blog drafts | draft PR |

Agents also report **discovered work** (new todos) on every run, which the platform validates, dedupes, and enqueues — the flywheel that keeps the fleet finding its own work.

## 🧭 How it works

```
        ┌────────────────────────── one cycle ──────────────────────────┐
        │                                                               │
 roster ─▶ DISCOVER ─▶ QUEUE (dedup) ─▶ SCHEDULE ─▶ DISPATCH ─▶ MONITOR │
 (wtd.yml)  issues       work items      priority +   Claude     ledger │
            PRs, CI,     ("todos")       per-repo     Code OAuth  runs, │
            docs gaps         ▲          fairness       ▼ fallback budgets
                              │                     Anthropic API       │
                              └──── agents add discovered todos ────────┘
```

1. **Discover** — deterministic scanners sweep the roster: issues, PRs, workflow runs, docs gaps.
2. **Queue** — findings become work items with stable dedup keys; rescans converge instead of duplicating.
3. **Schedule** — a pure scheduler matches items to agent roles: priority bands, oldest first, repos interleaved round-robin so one noisy repo can't starve the rest.
4. **Balance** — each provider lane has a daily token budget; the balancer picks the first lane with headroom (subscription first, API spillover), benches rate-limited lanes, and tracks burn.
5. **Dispatch** — the agent gets bounded evidence and must reply in a structured JSON contract; the platform validates every requested action against the role's grants before touching GitHub.
6. **Monitor** — every run lands in a ledger; `wtd fleet status` shows queue, budgets, lanes, and outcomes.

## 🚀 Quick start

```bash
# Install
pip install -e .

# 1. Auth (pick at least one lane)
claude setup-token          # default lane: Claude Code OAuth (subscription)
export CLAUDE_CODE_OAUTH_TOKEN=...
export ANTHROPIC_API_KEY=sk-ant-...   # fallback lane: Anthropic API

# 2. GitHub (read for discovery; write scopes only needed for --apply)
export GITHUB_TOKEN=ghp_...

# 3. Configure the fleet
wtd fleet init              # writes a starter wtd.yml + agents/
$EDITOR wtd.yml             # add your repos to fleet.repos

# 4. Fly
wtd fleet status            # lanes, roster, queue, budgets
wtd fleet run               # ONE cycle, dry-run (agents think, nothing is written)
wtd fleet run --apply       # let the agents act
wtd fleet loop --apply      # the autonomous daemon (Ctrl+C to stop)
```

Dry-run is the default everywhere: without `--apply` (or `WTD_FLEET_APPLY=true`) agents plan and their intended actions are recorded, but **nothing is written to GitHub**.

## 📖 Commands

| Command | Description |
|---------|-------------|
| `wtd fleet status` | Fleet health: provider lanes, roster, queue, budgets, recent runs |
| `wtd fleet agents` | List agent roles (built-ins + `agents/*.md` overrides) |
| `wtd fleet discover` | Scan the roster and enqueue new work (no agents run) |
| `wtd fleet plan` | Show what the next cycle would run |
| `wtd fleet run [--apply]` | Run one cycle: discover → schedule → dispatch |
| `wtd fleet loop [--apply]` | The autonomous loop (interval from config) |
| `wtd fleet queue` | Show the cross-repo work queue |
| `wtd fleet budget` | Today's token budgets and burn per lane |
| `wtd fleet init` | Write starter `wtd.yml` + `agents/` here |
| `wtd` | Classic mode: scan the local repo's TODOs into a recursive tree |
| `wtd scan` / `wtd dashboard` / `wtd execute` / `wtd status` | Local TODO tree tools |
| `wtd routines …` | Recurring TODO management |
| `wtd serve` | REST API (`/v1/fleet/*` + local tree endpoints) |

## 🔌 Providers: Claude-first, by design

The default `WTD_LLM_PROVIDER=auto` resolves this chain:

1. **`claude-code`** — headless Claude Code (`claude -p`), authenticated by `CLAUDE_CODE_OAUTH_TOKEN` (or an interactive `claude` login). Runs on your Claude subscription.
2. **`anthropic`** — the Anthropic API via the official SDK (`ANTHROPIC_API_KEY`), default model `claude-opus-5`, streaming + adaptive thinking, with server-side refusal fallbacks enabled on Opus 5.

Failover is automatic: rate limits, timeouts, and transient errors on one lane roll to the next, and the balancer's budgets decide which lane a run *starts* on. Legacy providers (`ollama`, `openai`) remain available when selected explicitly, but are never part of the auto chain.

## ⚙️ Configuration

Secrets live in the environment (see [`env.example`](env.example)); the fleet's shape lives in committable [`wtd.yml`](wtd.yml.example):

```yaml
fleet:
  repos:
    - repo: you/your-app
      roles: [triage, reviewer, bug-hunter, janitor]
    - repo: you/your-blog
      roles: [author]
      articles: true
  max_runs_per_cycle: 8
  max_writes_per_cycle: 5
  budgets:
    claude_code_daily_tokens: 1500000
    anthropic_daily_tokens: 500000
    anthropic_daily_usd: 10
```

Key environment switches:

```bash
WTD_FLEET_ENABLED=true      # master kill switch
WTD_FLEET_APPLY=false       # THE write gate (dry-run by default)
WTD_FLEET_CONCURRENCY=2     # concurrent agent runs
WTD_MODEL=claude-opus-5     # default model for both lanes
```

## 🛡️ Safety rails

The fleet is autonomous, so the guardrails are structural, not aspirational:

- **Dry-run by default** — writes require an explicit `--apply` / `WTD_FLEET_APPLY=true`.
- **Kill switch** — `WTD_FLEET_ENABLED=false` stops everything, including discovery.
- **Least privilege** — each role declares the only actions it may request; everything else an agent asks for is rejected and logged.
- **Loop guards** — the fleet never acts on its own issues/PRs/comments (marker `<!-- wtd-fleet:… -->` + author checks), so agents can't feed themselves.
- **Dedup everywhere** — stable work-item keys, marker-checked comments and issues; re-running never double-posts.
- **Hard caps** — per-cycle run cap, per-cycle write cap, per-run action cap (3), per-run discovered-todo cap, retry cap.
- **Path safety** — proposed PRs can't escape the repo, and can never write `.github/workflows/` (that would hand the agent its own credentials).
- **Token budgets** — daily per-lane budgets + an estimated-USD cap on the API lane; exhausted budgets defer work instead of overspending.
- **Prompt-injection posture** — repository content is fenced as untrusted in every prompt, and roles are instructed to ignore embedded instructions.

## 🖥️ Running it as a service

**Daemon**: `wtd fleet loop --apply` (systemd, tmux, whatever you run daemons with).

**Docker**:

```bash
docker build -t wtd .
docker run --rm -e CLAUDE_CODE_OAUTH_TOKEN -e ANTHROPIC_API_KEY -e GITHUB_TOKEN \
  -v $PWD/wtd.yml:/app/wtd.yml:ro -v wtd-state:/root/.wtd wtd fleet loop --apply
```

**GitHub Actions** (serverless): [`.github/workflows/fleet-loop.yml`](.github/workflows/fleet-loop.yml) runs one cycle every 4 hours — default-OFF until the repo variable `WTD_FLEET_ENABLED` is set to `true`, OAuth-first secrets, state cached between runs.

**REST API**: `wtd serve` → `GET /v1/fleet/status`, `GET /v1/fleet/queue`, `GET /v1/fleet/runs`, `POST /v1/fleet/discover`, `POST /v1/fleet/run` (the API can narrow to dry-run but can never escalate to writes; docs at `http://localhost:8787/docs`).

## 🤖 Custom agents

Drop `agents/<name>.md` files (YAML frontmatter + system prompt) into your project or `~/.wtd/agents/` to override built-ins or add specialists — see `wtd fleet init` for a template. Roles declare which work kinds they handle, which actions they may request, and their token cost envelope for the balancer.

## 🌳 The classic recursive TODO engine

The original local-first engine is still here and still the substrate: `wtd` scans the current repo (TODO/FIXME comments, markdown checklists), builds the recursive tree in `tree.json`, and can break tasks down with the same Claude-first provider chain. See `wtd --help`.

## 🧪 Development

```bash
pip install -e ".[dev]"
pytest            # 148 tests, all offline (fake GitHub + fake providers)
ruff check .
mypy wtd          # advisory
```

The load balancer, scheduler, outcome validator, and discovery scanners are pure and fully unit-tested; the dispatcher and orchestrator are integration-tested against an in-memory GitHub.

## 📚 More

- [`docs/FLEET.md`](docs/FLEET.md) — full architecture: subsystems, data flow, contracts, design decisions
- [`PRD.md`](PRD.md) — the original product vision (v0.1)
- [`CHANGELOG.md`](CHANGELOG.md) — release history

## 📄 License

MIT License — see [LICENSE](LICENSE).

---

**Reality WTD'd.** The fleet finds the work. The fleet does the work. The fleet finds more work.

*Ship it.* 🚀
