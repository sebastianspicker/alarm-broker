# Roadmap

## Guiding Principles

- No breaking changes to existing public endpoints.
- Stability and API consistency take precedence over feature breadth.
- UI improvements are additive and compatible with existing paths.

## Completed

### Phase 0: Baseline
- Lint and test baseline established.
- API and UI regressions covered by dedicated tests.

### Phase 1: Bug Fixes
- Fixed double-retry in connector base (3x3=9 retries -> 3).
- Fixed Signal dispatch routing through SMS handler.
- Fixed hardcoded Zammad values; now read from connector config.
- Removed unused TypeVar.

### Phase 2: Repository & Documentation Cleanup
- Removed tracked screenshots from git, added to `.gitignore`.
- Fixed `.DS_Store` gitignore pattern.
- Consolidated docs: merged INSTALL+DEVELOPMENT into SETUP, removed DATA_MODEL/DEMO_SCREENSHOTS/TROUBLESHOOTING.
- Translated German code comments and docstrings to English.

### Phase 3: Code Deduplication
- Unified connector naming: renamed `*Connector` classes to canonical `*Client`, removed backward-compat aliases.
- Removed `send_escalation_step` forwarding wrapper; callers use `send()` directly.
- Deduplicated Zammad ticket payload construction via `_build_notification_payload`.
- Fixed latent `ticket_id=` keyword arg bug in `_log_notification_result` call.

### Phase 4: Settings Refactoring
- Removed 9 unused group setting classes and 8 `@property` methods.
- Flat `Settings` class with validators and convenience `is_*_enabled()` methods.
- Reduced from ~397 lines to ~121 lines.

### Phase 5: Code Quality
- Moved `import httpx` from function-level to module-level in notification service.
- Replaced manual webhook retry loop with `tenacity` decorator.

### Phase 6: UI Improvements
- Unified ACK page design system (Inter + JetBrains Mono fonts, consistent CSS variables, aligned colors).
- Replaced `window.prompt()` with accessible inline confirmation modal (name + optional note).
- Added loading states on action buttons (disable + "..." text during async calls).
- Added success toast with auto-reload after 1.5s.
- Added empty state message when search filter matches no alarms.

### Phase 7: Final Validation
- All 49 tests pass, ruff clean.
- Added targeted regression test: connector retry count (exactly 3 attempts).

## Backlog

1. Internal refactoring of large modules (`alarms.py`, `notification_service.py`, `trigger_service.py`, `worker/tasks.py`) into smaller units.
2. Extended search/filter options for admin operations.
3. TypedDict definitions for structured internal dictionaries.
4. Exception handler consolidation in `api/main.py` (if pattern grows further).
5. Additional integration tests for escalation scheduling and multi-channel dispatch.

## Definition of Done

- Consolidated documentation in `docs/` with `docs/README.md` as index.
- Unified Notes route and stable simulation endpoints with tests.
- UI flows without known runtime errors in core paths.
- Full green quality gates (lint + tests).
