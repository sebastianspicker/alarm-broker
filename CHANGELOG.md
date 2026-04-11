# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Dark Mode support in Admin UI (auto-detects system preference)
- Configurable webhook retry settings (`WEBHOOK_MAX_RETRIES`, `WEBHOOK_RETRY_DELAY_SECONDS`)
- Externalized Admin UI HTML template for better maintainability
- Dockerfile multi-stage build for optimized image size
- `pytest-cov` for test coverage measurement with 75% threshold enforcement
- `EnrichedAlarmContext` and `NotificationPayload` TypedDicts for type-safe internal data
- 148 new tests (197 total) covering all services, connectors, worker tasks, bulk ops, pagination, admin UI
- `[tool.mypy]` configuration with strict checks in pyproject.toml
- CSV export formula injection protection
- `services/message_formatter.py` (canonical location for alarm message formatting)
- `services/metrics_queries.py` (DB queries for Prometheus metrics)

### Changed
- POST create endpoints (devices, escalation policy) now return 201 Created
- Added docstrings to all API endpoints for OpenAPI documentation
- Split `alarms.py` (781 lines) into `alarms.py`, `alarm_operations.py`, `alarm_notes.py`
- Service layer now uses domain exceptions (`ConflictError`, `NotFoundError`, `ValidationError`) instead of `HTTPException`
- mypy type checking enforced in CI (removed `|| true`)
- `core/metrics.py` decoupled from `db/models` — accepts data as parameters
- Coverage threshold raised to 90% (actual: 92%)
- Fixed coverage measurement: `concurrency = ["greenlet", "thread"]` for async tracing
- Test imports use try/except pattern for directory-independent execution
- `AlarmPatchSchema` now enforces length limits on title, description, tags
- Improved code deduplication in API routes
- Refactored webhook logic into separate functions for better testability

### Fixed
- Fixed soft-deleted alarms appearing in list, export, dashboard, and bulk operations
- Fixed export_alarms Content-Disposition header on StreamingResponse
- Fixed hardcoded status strings in admin_ui.py to use AlarmStatus enum
- Fixed 54 mypy type errors across 20 files
- Fixed `seed.py` type inference with entity-specific variable names
- Fixed `settings.py` AnyHttpUrl/str type mismatch
- Fixed `mock.py` class-level type annotations
- Fixed broken documentation links
- Removed duplicate PUT /devices endpoint

## [0.1.0] - 2024-01-15

### Added
- Initial release
- Alarm management API (create, list, acknowledge)
- Admin UI for alarm management
- Notification connectors: SendXMS (SMS), Signal, Zammad
- Webhook notifications
- Idempotency handling
- Rate limiting
- IP allowlist
- Seed data loading
- Health and readiness checks
- Alarm simulation for testing
- FastAPI REST API with PostgreSQL (SQLAlchemy), Redis, arq worker, Alembic migrations
