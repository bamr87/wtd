# Contributing to WTD

Thanks for working on WTD. This guide covers the local setup, the checks CI
runs, and — most importantly — the **self-healing CI contract**, which is the
one piece of this repository that will surprise you if nobody tells you about
it in advance.

---

## 🤖 Self-healing CI: the bot may push to your branch

**Read this before your second push to a pull request.**

This repository runs a `markdown-oneline` workflow
([`.github/workflows/markdown-oneline.yml`](.github/workflows/markdown-oneline.yml))
that keeps prose in Markdown files on single, unwrapped lines. When it finds
wrapped prose in a pull request, it does not simply fail the build — for
branches inside this repository it **repairs the files, commits the fix, and
pushes that commit to your pull request branch** using the workflow's
`GITHUB_TOKEN`.

So the contract is:

> Your PR branch on the remote can move without you moving it.

### What this means in practice

**1. Always `git pull --rebase` before you push again.**

After opening or updating a PR, assume the remote branch may be one commit
ahead of your local copy. Before your next push:

```bash
git pull --rebase origin <your-branch>
git push
```

If you skip this, your next `git push` is rejected with the familiar
`! [rejected] ... (fetch first)` / `non-fast-forward` error. That is not a
broken repository — it is the bot's commit sitting on the remote branch. Pull
with `--rebase` and push again.

A plain `git pull` (merge) also works, but it leaves a merge commit in the PR
for a one-line formatting fix. Rebasing keeps the history readable.

**Do not force-push over the bot's commit** unless you have deliberately
re-applied the same formatting locally. A `git push --force` that drops the
repair simply causes CI to make the fix — and the extra commit — all over
again.

**2. Pull requests from forks do not get auto-repair.**

For a pull request opened from a fork, GitHub gives the workflow a **read-only**
`GITHUB_TOKEN`. The workflow cannot push back to your fork's branch, so the
self-healing step cannot run and the check reports the problem instead of
fixing it.

If you are contributing from a fork — which is the normal path for external
contributors — run the repair yourself before you push:

```bash
python3 tools/unwrap-prose.py --write
git add -A
git commit -m "docs: unwrap prose lines"
git push
```

The same command is worth running locally even inside this repository: it
keeps the diff yours, keeps the review history clean, and saves a CI round
trip.

**3. Check before you commit.**

Run the tool without `--write` to see what it *would* change without touching
your working tree:

```bash
python3 tools/unwrap-prose.py
```

### Quick reference

| Situation | What happens | What you do |
|---|---|---|
| PR from a branch in this repo | CI commits and pushes the fix to your branch | `git pull --rebase` before your next push |
| PR from a fork | Read-only token; CI reports, cannot fix | Run `python3 tools/unwrap-prose.py --write` locally, commit, push |
| Before any push | — | `python3 tools/unwrap-prose.py` to preview changes |

---

## Getting set up

WTD targets **Python 3.10 or newer** and is tested against 3.10, 3.11 and 3.12
(see the classifiers in [`pyproject.toml`](pyproject.toml)).

```bash
git clone https://github.com/bamr87/wtd.git
cd wtd

python3 -m venv .venv
source .venv/bin/activate

# Editable install with the development toolchain
pip install -e '.[dev]'
```

The `dev` extra pulls in everything the checks below need: `pytest`,
`pytest-asyncio`, `ruff`, and `mypy`, plus the optional `openai` and `ollama`
providers so provider-related tests can import cleanly.

The install exposes the `wtd` console script (declared under
`[project.scripts]`), so `wtd --help` should work once the virtualenv is
active.

Runtime configuration and secrets live in the environment — copy
[`env.example`](env.example) as a starting point — while the fleet's shape
lives in a committable `wtd.yml` (see [`wtd.yml.example`](wtd.yml.example)).

---

## The checks

CI runs the same three checks you can run locally. Run all of them before
opening a pull request.

### Tests

```bash
pytest
```

Configuration lives in `[tool.pytest.ini_options]` in `pyproject.toml`:
discovery is rooted at `tests/`, `asyncio_mode` is `auto` (so `async def`
tests need no decorator), and `--strict-markers` is on — an unregistered
`@pytest.mark.*` is an error, not a warning.

### Lint

```bash
ruff check .
```

Ruff is configured for a 100-character line length targeting `py310`, with the
`E`, `F`, `I`, `N` and `W` rule sets enabled.

A handful of older modules are listed under `extend-exclude` in
`pyproject.toml` pending a formatting cleanup pass. Two ground rules apply to
that list:

- **New code is fully linted.** Do not add new modules to the exclude list.
- **The list should shrink, never grow.** If you are already touching an
  excluded module, cleaning it up and removing its entry is a very welcome
  change — ideally as its own commit, separate from behavioural work.

Long multi-line LLM prompt strings are exempt from `E501` in
`wtd/core/agent.py` and `wtd/fleet/roles.py`; that exemption is per-file and
deliberate.

### Types

```bash
mypy .
```

**CI gates on mypy.** The codebase is currently clean at the configured
settings (`strict = false`, but with `check_untyped_defs`,
`warn_unused_ignores`, `warn_redundant_casts` and `no_implicit_optional`
enabled), so a new type error will fail the build. Full `strict = true` is the
eventual goal, tightened one flag at a time while keeping the error count at
zero.

---

## Pull request workflow

1. Branch from `main`.
2. Make your change, with tests where the behaviour is testable.
3. Run `ruff check .`, `mypy .` and `pytest` locally.
4. If you touched Markdown — and especially if you are working from a fork —
   run `python3 tools/unwrap-prose.py --write` and commit the result.
5. Open the pull request against `main`.
6. **Before every subsequent push, `git pull --rebase`** — CI may have added a
   commit to your branch (see the self-healing section above).

Keep pull requests focused. A documentation-only change, a lint cleanup, and a
behaviour change are three pull requests, not one.

---

## Where to look next

- [`README.md`](README.md) — what WTD is, the agent roles, and the CLI surface
- [`docs/FLEET.md`](docs/FLEET.md) — the fleet documentation linked from the
  project metadata
- [`CLAUDE.md`](CLAUDE.md) — guidance for AI agents working in this repository
- [`CHANGELOG.md`](CHANGELOG.md) — release history

WTD is itself an autonomous agent platform, so some pull requests in this
repository are opened by the fleet. They follow the same rules as everyone
else's: they are drafts, they get reviewed, and they get the same three checks.
