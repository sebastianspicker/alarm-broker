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
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local development setup, testing, and contribution guide |

## Quick Links

- [Main README](../README.md) - Project overview and quickstart
- [Improvement Plan](../plans/improvement-plan.md) - Roadmap for enhancements
- [Security Findings Archive](archive/DEEP_CODE_INSPECTION_FINDINGS.md) - Historical security fixes
