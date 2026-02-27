# Alarm Broker Documentation

## Overview

This repository implements a PoC alarm broker that receives silent alarms and fans out notifications across multiple channels while persisting an auditable lifecycle.

**⚠️ DISCLAIMER:** This project is work in progress and not production-ready. Do not use in safety-critical environments.

## Documentation Index

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, flows, lifecycle, operational endpoints |
| [DATA_MODEL.md](DATA_MODEL.md) | Persisted entities, lifecycle columns, migration overview |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Yealink/Zammad templates and connector notes |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local development setup, quality gates, and contributor workflow |
| [INSTALL.md](INSTALL.md) | Installation and first-time setup |
| [OPERATIONS.md](OPERATIONS.md) | Operational runbook, monitoring, and maintenance |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common errors and remediation playbooks |
| [ROADMAP.md](ROADMAP.md) | Consolidated implementation roadmap and technical debt backlog |
| [DEMO_SCREENSHOTS.md](DEMO_SCREENSHOTS.md) | Local Mock University demo workflow and screenshot runbook |

## Quick Links

- [Main README](../README.md) - Project overview and quickstart
- [Security Policy](../SECURITY.md) - Security best practices and disclosure process
- [Changelog](../CHANGELOG.md) - Release and change history

## Notes

- Temporary planning artifacts from `plans/` were consolidated into [ROADMAP.md](ROADMAP.md).
- The documentation index in this file is the canonical list of maintained docs.
