# WTD Ultimate Checklist (Always Referenced)

This is the **canonical checklist** for *every* TODO task and *every* change in the repo.
If it’s not satisfied here, it’s not “done”.

## Reference IDs (Required)

Every task/change MUST have a stable reference ID. Use one of:
- **BUG-####** (bug fixes)
- **FEAT-####** (new features / behavior changes)
- **DOC-####** (documentation changes)
- **CHORE-####** (maintenance / refactors / tooling)
- **SEC-####** (security changes)
- **PERF-####** (performance changes)

### How to assign an ID
- **Default**: next number in sequence per prefix (keep it simple).
- **Where it must appear**:
  - **Commit message**: begins with the ID (ex: `BUG-0042: fix scanner false positives`)
  - **Changelog** (if user-facing behavior changed): include the ID in the bullet
  - **tree.json node metadata** when applicable (see “tree.json rules”)

### One change, one ID (mostly)
- Prefer **one primary ID per PR/release commit**.
- If a change spans multiple IDs, keep **one primary** and list secondary IDs in the PR description/release notes.

---

## Universal Definition of Done (DoD)

### Scope & intent
- [ ] **ID assigned** (BUG/FEAT/DOC/CHORE/SEC/PERF).
- [ ] **Problem statement** written in 1–3 sentences.
- [ ] **Success criteria** written and measurable (what “done” means).
- [ ] **Non-goals** noted (what will NOT be done right now).

### Implementation hygiene
- [ ] **Smallest change that solves it** (avoid “while I’m here” unless it’s a CHORE with its own ID).
- [ ] **No silent failures**: errors are surfaced, logged, or returned to caller.
- [ ] **No data loss**: migrations/backward compatibility considered if storage format changes.
- [ ] **No breaking CLI/API changes** without bump + release note callout.

### Verification
- [ ] **Repro steps** for bugs documented (before + after).
- [ ] **Tests** added/updated if the change impacts logic or parsing.
- [ ] **Manual verification** performed if UX/UI/CLI output changes.
- [ ] **Edge cases** considered (empty inputs, weird files, missing deps, permission errors).

### Documentation
- [ ] **README** updated if usage/behavior changes.
- [ ] **CHANGELOG** updated if user-facing behavior changed.
- [ ] **Release notes** updated if it’s going out as a tagged release.

---

## `tree.json` Rules (Core Repo DNA)

`tree.json` is the system of record and must remain:
- [ ] **Portable** (no local absolute paths like `/Users/...`).
- [ ] **Deterministic** for scanned TODO identity (stable across runs).
- [ ] **Append-only-ish**: history grows, state updates, nothing “mysteriously disappears”.
- [ ] **Schema versioned**: any format change increments schema version and is documented.

### When to touch `tree.json`
- [ ] Any command that scans, executes, spawns, completes, cancels, or updates task state must:
  - [ ] **Load → merge/apply → save**.
- [ ] Any new feature that introduces task metadata must:
  - [ ] Add it under `nodes[*].metadata` (avoid breaking core fields).

### Node identity & references
- [ ] **Scanned TODOs** must have stable `node_id` derived from source + content.
- [ ] **Generated/spawned TODOs** must have stable linkage:
  - [ ] Store `parent_node_id` relationship.
  - [ ] Preserve history events on status transitions.

### Events & audits
- [ ] Each meaningful mutation emits an event:
  - [ ] `scan_merged`, `tree_applied`, `status_changed`, etc.
- [ ] For bugfixes, store an event with the BUG ID in node history or store events when relevant.

---

## Bug Fix Checklist (BUG-####) — Required extras

### Tracking & references
- [ ] **BUG-#### assigned** and used in the commit title.
- [ ] **Reference number included** in:
  - [ ] `CHANGELOG.md` entry (under **Fixed** or appropriate section)
  - [ ] Release notes (if releasing)
- [ ] **Root cause** documented (1–5 bullets).

### Verification
- [ ] **Repro before**: clear steps or failing case captured.
- [ ] **Fix after**: steps re-run and confirmed.
- [ ] **Regression guard**:
  - [ ] Add a test or a deterministic check for the failure mode.

### Output hygiene
- [ ] No new noisy output by default; logs/errors are purposeful.

---

## Feature Checklist (FEAT-####) — Required extras

- [ ] **FEAT-#### assigned** and used in commit title.
- [ ] **UX impact** documented:
  - [ ] CLI flags/commands
  - [ ] API endpoints/contracts
  - [ ] Dashboard behavior
- [ ] **Backward compatibility** considered and explicitly stated.
- [ ] **Docs** updated (README + examples).
- [ ] **Changelog** entry added under **Added/Changed**.

---

## Documentation Checklist (DOC-####)

- [ ] **DOC-#### assigned** if the doc change is significant.
- [ ] Screenshots/examples updated (if referenced).
- [ ] Commands in docs are runnable (copy/paste).

---

## Changelog Rules (`CHANGELOG.md`)

Update the changelog when the change is user-visible:
- [ ] Added/Changed/Fixed/Deprecated/Removed/Security section used correctly.
- [ ] Each entry includes the **ID**: `BUG-####`, `FEAT-####`, etc.
- [ ] Entry is written as an outcome (what changed for the user), not an implementation detail.

---

## Release Rules (tags + GitHub release)

- [ ] Version bumped in:
  - [ ] `pyproject.toml`
  - [ ] `wtd/__init__.py`
- [ ] `CHANGELOG.md` updated for the release.
- [ ] Release notes file created/updated (ex: `RELEASE_NOTES_0.x.y.md`).
- [ ] Tag created: `vX.Y.Z` (annotated).
- [ ] GitHub Release created using release notes.

---

## Commit Message Rules

- [ ] Starts with **ID**: `BUG-####: ...` / `FEAT-####: ...`
- [ ] One sentence “what” (imperative) + optional short “why” in body.
- [ ] No “misc” / “wip” on main.

---

## Quick Templates

### Bug entry (CHANGELOG)
- `- **BUG-####**: Fix <user-visible symptom> when <condition>.`

### Feature entry (CHANGELOG)
- `- **FEAT-####**: Add <capability> to <surface> (CLI/API/UI).`

### Commit title
- `BUG-####: fix <symptom> in <module>`
- `FEAT-####: add <capability>`

