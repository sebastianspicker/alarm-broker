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

### Changed
- Improved code deduplication in API routes
- Refactored webhook logic into separate functions for better testability

### Fixed
- Fixed broken documentation links
- Removed duplicate PUT /devices endpoint

## [0.1.0] - 2024-01-15

### Added
- Initial release
- Alarm management API (create, list, acknowledge)
- Admin UI for alarm management
- Multiple notification connectors (SendXMS, Signal, Zammad)
- Webhook notifications
- Idempotency handling
- Rate limiting
- IP allowlist
- Seed data loading
- Health and readiness checks
- Alarm simulation for testing

### Architecture
- FastAPI-based REST API
- PostgreSQL database with SQLAlchemy ORM
- Redis for idempotency and rate limiting
- arq-based async worker for background tasks
- Alembic database migrations
