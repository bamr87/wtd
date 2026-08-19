# WTD Project TODOs

This directory contains active TODO items for the WTD project. (The fleet's own scanners read markdown checklists like these — the platform dogfoods its namesake.)

## Active Tasks

- [ ] Fleet: ETag-conditional GitHub polling to stretch rate limits
- [ ] Fleet: `improve_code` discovery from remote repos (currently local-scan evidence only)
- [ ] Fleet: workspace-mode Claude Code runs (agentic lane with repo checkout + narrow tool grants) behind a per-role opt-in
- [ ] Fleet: per-role model tiers (e.g. haiku for triage) once budget history exists
- [ ] Fleet: surface `wtd fleet status` as a static HTML report for Pages
- [ ] Add support for Notion and Obsidian notes
- [ ] Create VSCode extension for seamless integration
- [ ] Implement cross-machine sync via cloud storage

## Completed

- [x] Build core TODO scanner
- [x] Implement recursive TODO tree
- [x] Create CLI interface
- [x] Add REST API endpoints
- [x] Build interactive dashboard
- [x] Add GitHub issues integration for scanning remote TODOs (fleet discovery, 0.2.0)
- [x] Wire everything through Claude Code OAuth with Anthropic API fallback (0.2.0)
- [x] Autonomous fleet orchestrator with token-capacity load balancing (0.2.0)
