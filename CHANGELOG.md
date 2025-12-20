# Changelog

All notable changes to **WTD (What To Do)** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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


