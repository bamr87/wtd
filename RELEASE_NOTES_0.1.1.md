# Release Notes — 0.1.1

## Core change: `tree.json` is now the repo’s durable “TODO DNA”

This release makes **`tree.json` the always-updated, always-used system of record** for WTD.

### What’s new
- **Persistent repo memory**: scans and TODO state are merged into `tree.json` every run.
- **History & references**:
  - Scan history (`scans[]`) with timestamps, counts, confidence, and context
  - Per-node history (`nodes[...].history[]`) for status changes and “seen” events
  - Indexes (`indexes.*`) for fast lookup by file/tag/context/status
- **Portable storage**: `tree.json` is written without leaking local absolute paths.

### Updated surfaces
- **CLI** (`wtd` / `wtd scan` / `wtd status` / `wtd execute` / `wtd dashboard`):
  - Always loads + updates `tree.json`
  - Builds the runtime tree from the store
  - Persists execution changes back to disk
- **Dashboard**: execute/complete/cancel now persist to `tree.json`.
- **API**: `/v1/wtd/scan`, `/v1/wtd/execute`, `/v1/wtd/spawn`, `/v1/wtd/complete/...` persist changes to `tree.json`.

### Notable implementation details
- Added `wtd/core/tree_store.py` for store load/merge/save and TodoTree reconstruction.
- `TodoTree.to_dict()` now uses Pydantic JSON mode for safer serialization.


