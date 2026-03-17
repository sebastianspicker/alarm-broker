# Audit Progress

## Completed

### Item 1: Fix obfuscated `__import__("enum")` in models.py
- **File**: `services/alarm_broker/alarm_broker/db/models.py`
- **What**: `AlarmStatus` was defined as `class AlarmStatus(__import__("enum").StrEnum)` — an inline dynamic import that bypasses normal import machinery and lint tooling.
- **Fix**: Added `import enum` at the top of the file and changed the class definition to `class AlarmStatus(enum.StrEnum)`.
- **Why better**: Standard imports are readable, lint-friendly, and consistently resolved. `__import__()` is for programmatic meta-import scenarios, not inline class inheritance.

### Item 2: Dead code in `notification_service.py::_send_sms_notifications`
- **File**: `services/alarm_broker/alarm_broker/services/notification_service.py`
- **What**: The method contained an unreachable `if target.channel == "signal"` block — this method is only dispatched when `target.channel == "sms"`, making the signal branch permanently false. The subsequent `if target.channel == "sms"` check was always true. Both conditions were dead/redundant.
- **Fix**: Simplified to a single direct `_send_via_sendxms` call. Removed misleading docstring that said "Tries Signal first".
- **Why better**: Removes false control-flow impressions. The routing is already done by `_send_to_channel`; repeating it here was copy-paste drift.

### Item 3: Remove thin wrapper methods in `trigger_service.py`
- **File**: `services/alarm_broker/alarm_broker/services/trigger_service.py`
- **What**: Three private methods (`_check_rate_limit`, `_enrich_trigger_data`, `_create_alarm`) were pure pass-through delegators to identically-named public methods (`check_rate_limit`, `validate_device`, `create_alarm`) with the same arguments.
- **Fix**: Removed the three wrapper methods; `process_trigger` now calls the public methods directly.
- **Why better**: Eliminates unnecessary indirection and halves the number of methods. `_evaluate_policies` (which has real logic/TODO) was kept.

### Item 4: Extract duplicated Zammad ticket payload builder in `notification_service.py`
- **File**: `services/alarm_broker/alarm_broker/services/notification_service.py`
- **What**: `_send_email_notifications` and `handle_zammad_ticket` each independently constructed the identical Zammad ticket dict (title, group, priority_id, state_id, customer_id, tags, article). Any future change required updating both places.
- **Fix**: Extracted `_build_zammad_ticket_payload(self, payload)` helper and replaced both inline dicts with calls to it.
- **Why better**: Single source of truth for the Zammad ticket structure. Changes (new field, different article subject) now happen in one place.

### Item 5: Remove unused ALL_CAPS functions from `constants.py`
- **File**: `services/alarm_broker/alarm_broker/constants.py`
- **What**: Four functions (`EMERGENCY_ALARM_TITLE`, `ALARM_ACKNOWLEDGED_TITLE`, `ALARM_RESOLVED_TITLE`, `ALARM_CANCELLED_TITLE`) were defined using ALL_CAPS naming (Python's convention for module constants, not functions). Grep confirmed zero call-sites anywhere in the codebase — pure dead code.
- **Fix**: Deleted all four functions and the `# Notification Messages` section comment.
- **Why better**: Removes dead code that violated naming conventions and mislead readers into thinking they were looking at constants.

### Item 6: Fix naive `datetime.now()` and cleanup in `alarms.py` + `tasks.py`
- **Files**: `services/alarm_broker/alarm_broker/api/routes/alarms.py`, `services/alarm_broker/alarm_broker/worker/tasks.py`
- **What (bug)**: `alarm.deleted_at = datetime.now()` used a naive datetime. The `deleted_at` column is `DateTime(timezone=True)`; all other timestamps in the codebase use `datetime.now(UTC)`. A naive datetime stored in a `TIMESTAMPTZ` column causes silent coercion issues.
- **Fix**: Added `UTC` to the import and changed to `datetime.now(UTC)`.
- **What (deferred import)**: `from sqlalchemy import func` was deferred inside `alarm_stats()` with no circular-import reason. Moved to the module-level sqlalchemy import.
- **What (defensive getattr)**: `getattr(settings, "webhook_url", None)` and `getattr(settings, "webhook_enabled", False)` in `tasks.py` used defensive attribute access against a fully typed `Settings` object. Replaced with `settings.is_webhook_enabled()` (already defined for this exact purpose) and `settings.webhook_url`.

### Item 7: Remove unused `session_scope` + fix variable shadowing in `metrics.py`
- **Files**: `services/alarm_broker/alarm_broker/db/session.py`, `services/alarm_broker/alarm_broker/core/metrics.py`
- **What (dead code)**: `session_scope` in `db/session.py` was defined but never imported or called anywhere in the project. All callers use `sessionmaker()` as context manager directly.
- **Fix**: Removed `session_scope` and the now-unused `AsyncIterator` import.
- **What (shadowing)**: In `metrics.py::_alarm_counts`, `status` was used as the comprehension variable and immediately re-used as the for-loop variable, shadowing it with a semantically different binding.
- **Fix**: Renamed to `s` in the comprehension and `alarm_status` in the loop for unambiguous reading.

### Item 8: Fix missing `severity` in enriched context + type `_apply_alarm_filters`
- **Files**: `services/alarm_broker/alarm_broker/services/enrichment_service.py`, `services/alarm_broker/alarm_broker/api/routes/alarms.py`
- **What (bug)**: `enrich_alarm_context` never populated `severity` in the returned dict. `notification_service.py` calls `enriched.get("severity", constants.PRIORITY_CRITICAL)` — the default always fired, making alarm severity silently irrelevant to notification priority. P1/P2/P3 alarms were always treated as P0.
- **Fix**: Added `enriched["severity"] = alarm.severity` to `enrich_alarm_context`.
- **What (type hints)**: `_apply_alarm_filters` had no type annotations on any parameter or return value, defeating static analysis.
- **Fix**: Added `Select` return type and typed all parameters (`AlarmStatus | None`, `str | None`, `datetime | None`). Added `Select` to sqlalchemy imports.

### Item 9: Test cleanup — redundant decorators + duplicate helper
- **Files**: all 6 test files, `tests/conftest.py`
- **What (redundant decorators)**: `@pytest.mark.asyncio` appeared 36 times across all test files. `pyproject.toml` already sets `asyncio_mode = "auto"`, which makes the decorator completely redundant on every async test function.
- **Fix**: Removed all 36 occurrences with a single sed pass.
- **What (duplicated helper)**: `_trigger_alarm(client)` was identically defined in both `test_lifecycle_and_ops.py` and `test_notes_and_simulation.py`.
- **Fix**: Moved canonical `trigger_alarm` to `conftest.py`; both test files now import it as `_trigger_alarm` from there. Also added `uuid` and `AsyncClient` imports to `conftest.py`.

## Security Audit

### S1: Webhook secret sent as plaintext header instead of HMAC signature — HIGH
- **File**: `services/alarm_broker/alarm_broker/worker/tasks.py`
- **What (bug)**: `headers["X-Webhook-Secret"] = settings.webhook_secret` transmitted the raw secret value in every webhook delivery. The `.env.example` described this field as "for HMAC signature" but the implementation was literally a bearer-token-style passthrough. Any intercepted request reveals the permanent shared secret; no replay protection.
- **Fix**: Replaced with proper HMAC-SHA256 signing. The payload is serialized to deterministic JSON bytes once, then signed: `hmac.new(secret, payload_bytes, sha256).hexdigest()`. The signature is sent as `X-Hub-Signature-256: sha256=<hex>` (following the GitHub webhook standard). Receivers verify by computing the same HMAC over the raw request body and comparing. The pre-serialized `payload_bytes` are also passed to `_post_webhook` via `content=` to guarantee the signed bytes are what gets transmitted.
- **Also updated**: `.env.example` now documents the exact signing scheme and verification method for integrators.

### S2: ACK token logged in plaintext via request middleware — MEDIUM
- **File**: `services/alarm_broker/alarm_broker/api/main.py`
- **What (bug)**: The observability middleware logged `request.url.path` verbatim for every request. ACK tokens live in the URL path as `/a/<32-byte-urlsafe-token>`, so every ACK page visit emitted the full token in the structured log. ACK tokens are one-time-use credentials — logging them means any log aggregator, log rotation file, or SIEM system becomes a credential store.
- **Fix**: Added `_safe_log_path(path)` helper that returns `/a/{ack_token}` for any path matching `/a/...`. All log statements and Prometheus label calls now use `log_route` (the masked value). The actual routing and security headers middleware are unaffected — only the log and metrics output is sanitized.

### S3: Admin API key appears in Uvicorn access logs via query param — LOW
- **Context**: The admin dashboard is accessed at `/admin?key=<api_key>`. Uvicorn's default access log format logs the full request line including query parameters. If Uvicorn access logs are collected (stdout → log aggregator), the admin key appears in every admin page load entry.
- **Assessment**: Design limitation of query-param authentication. The custom structured logging middleware uses `request.url.path` (no query string) so our own logs are safe. Uvicorn's built-in access logger is the exposure vector. Requires: operator-level log access to exploit — admin key already implies admin access.
- **Recommendation**: Add `--no-access-log` to the Dockerfile CMD if strict log hygiene is required, at the cost of losing HTTP access log visibility. Alternatively, migrate the admin UI to POST-based key submission or an `Authorization` header-based session.
- **No code change made**: This is a known trade-off of simple key-in-URL admin UIs; the risk profile is low for an internal service.

### S4: Dependencies use minimum-version constraints (`>=`) rather than pinned hashes — LOW
- **File**: `services/alarm_broker/pyproject.toml`
- **What**: All runtime dependencies use `>=` floor constraints (e.g., `fastapi>=0.115`, `httpx>=0.27`). No CVEs found in current installed versions via `pip-audit` (checked against the global advisory database March 2026). No hash-pinned lockfile exists.
- **Assessment**: Unpinned `>=` constraints allow `pip install` to resolve any future compatible version, including ones that may introduce breaking changes or CVEs.
- **Recommendation**: Generate a `requirements.txt` with `pip freeze` or adopt `uv` lockfiles (`uv lock`) for reproducible builds. CI already includes `pip-audit` as a dev dependency — ensure it runs against installed requirements.
- **No code change made**: Adding lockfiles is an operational/CI concern, not a source-code change.

## Security Audit: Files Reviewed
All source files were read and analyzed. The following additional checks showed clean results:
- **Bandit** static analysis: 0 issues at MEDIUM+ severity/confidence
- **Dangerous calls**: No subprocess shell execution, eval, exec, or dynamic imports (the `__import__` in models.py was fixed in Item 1 of the code quality audit)
- **SQL injection**: All database access uses SQLAlchemy ORM with parameterized queries
- **Path traversal**: Template and seed file paths are module-relative constants, not user-controlled
- **SSRF**: Webhook URL is operator-set via environment variable (not user-controlled). `simulation_seed_url` is defined but never fetched.
- **YAML bomb/XXE**: Uses `yaml.safe_load` exclusively — safe against arbitrary code execution and entity bombs
- **XSS**: Admin UI template uses `html.escape()` consistently on all DB-sourced values; CSP header set to `default-src 'self'`
- **ACK token exposure**: Fixed in S2. Anti-caching headers also set for `/a/` routes to prevent browser caching of token pages.
- **Device token logging**: `_hash_token_for_logging()` ensures only SHA-256 prefix appears in logs, never raw token
- **Idempotency key storage**: SHA-256 hashed in Redis (both idempotency and rate-limit keys)
- **`secrets.compare_digest`**: Used correctly for both admin key comparison and admin UI key check
- **Trusted proxy X-Forwarded-For**: Only applied when peer IP is in configured trusted CIDR list — no IP spoofing via header
- **IP allowlist empty = allow-all**: Intentional for development flexibility; documented in `.env.example`

## Security Audit: Summary
All items from `02-security.md` scope have been reviewed. Two actionable fixes applied (S1, S2). Two informational LOW findings documented (S3, S4).

## Docs Audit

### D4: ARCHITECTURE.md — AI slop word + duplicated intro sentence
- **File**: `docs/ARCHITECTURE.md`
- **What**: The document opened with the same sentence as `docs/README.md` ("This repository implements a PoC..."), turning ARCHITECTURE.md into a self-introduction rather than jumping into content. Also, the Security baseline section used "Robust input parsing/validation" — "robust" is an AI writing tic.
- **Fix**: Removed the redundant opening sentence (the `docs/README.md` index is the right place for it). Replaced "Robust input parsing/validation" with a concrete description: "Input validation on admin seed and escalation policy operations (Pydantic + ORM only)."

### D3: SECURITY.md has two inaccuracies about security headers and pip-audit
- **File**: `SECURITY.md`
- **What**: Claimed "CORS configuration available" (no CORS middleware exists) and "Security headers can be added via middleware" (they're already unconditionally applied). Also showed `pip-audit services/alarm_broker` which isn't a valid invocation — pip-audit doesn't take a directory argument like that.
- **Fix**: Replaced the vague headers section with the actual headers that are always set. Added a note that CORS is not configured. Fixed the pip-audit command to `cd services/alarm_broker && pip-audit`.

### D2: OPERATIONS.md has incorrect metrics table and nonexistent config vars
- **File**: `docs/OPERATIONS.md`
- **What**: The metrics table listed four fabricated metric names (`alarm_broker_alarms_total`, `alarm_broker_webhook_duration_seconds`, `alarm_broker_active_alarms`), wrong types, and no labels. The actual metrics (from `core/metrics.py`) are `alarm_broker_http_requests_total`, `alarm_broker_http_request_duration_ms_total`, `alarm_broker_alarms_by_status`, `alarm_broker_notifications_total`, `alarm_broker_events_total`. The Performance Tuning section also listed five environment variables (`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, `WORKER_CONCURRENCY`, `REDIS_MAX_CONNECTIONS`) that don't exist in settings.py.
- **Fix**: Rewrote the metrics table to match actual code. Replaced the env var examples with accurate notes pointing to where these settings live in code.

### D11: Numbered-list docstring in `alarm_created` + restatement comment in `event_publisher.py`
- **Files**: `worker/tasks.py`, `services/event_publisher.py`
- **What**: `alarm_created` had a 4-step numbered docstring restating the function body. `EventPublisher.JOB_NAME` had `# Job name for processing alarm events` above it — the field name and value already say this.
- **Fix**: Collapsed `alarm_created` docstring to one descriptive line. Removed the class field comment.

### D10: "This module provides" restatement module docstrings in 7 files
- **Files**: `core/errors.py`, `core/logging.py`, `connectors/sendxms.py`, `connectors/zammad.py`, `api/routes/health.py`, `api/routes/yealink.py`, `api/routes/simulation.py`
- **What**: Each had a two-sentence (or bullet-list) module docstring where the second sentence restated the first in different words. Pattern: "X for Y. This module provides X for Y."
- **Kept verbosity in**: `connectors/base.py` ("centralizing retry logic, HTTP client handling"), `connectors/signal.py` ("signal-cli-rest-api"), `connectors/mock.py` ("store all sent notifications for later retrieval") — all add non-obvious specifics.
- **Fix**: Collapsed each to one precise line. `simulation.py` kept `(enabled only when SIMULATION_ENABLED=true)` since that's non-obvious operational context.

### D9: Obvious inline comments across remaining source files
- **Files**: `worker/tasks.py`, `api/routes/alarms.py`, `api/routes/admin_ui.py`, `api/routes/health.py`, `services/trigger_service.py`
- **What**: 10 comments restated their next line: `# Create Zammad ticket`, `# Schedule future escalation steps`, `# Get field names from first alarm`, `# Convert datetime to ISO format`, `# Verify alarm exists`, `# Build query with optional status filter`, `# Get counts (total and by status)`, `# Calculate time since creation`, `# Build filter query string for links`, `# Check if there's an existing alarm ID we can use`, `# Check connectivity`.
- **Kept across all files**: All WHY comments (race condition notes, simulation mode explanations, propagation note, etc.).
- **Fix**: Deleted all 11 restatement comments.

### D8: Obvious comments + numbered-list docstring in `notification_service.py`
- **File**: `services/alarm_broker/alarm_broker/services/notification_service.py`
- **What**: `send` had a numbered list (1–4) restating its body steps. Seven inline comments restated their next lines: `# Build notification payload`, `# Fetch escalation targets for this step`, `# Send to each target's preferred channel`, `# Build message body`, `# Determine severity-based priority`, `# Build title based on severity`, `# Set tags based on step and severity`.
- **Fix**: Collapsed docstring to one descriptive line. Removed all 7 comments.

### D7: Obvious comments + verbose docstring in `api/routes/yealink.py`
- **File**: `services/alarm_broker/alarm_broker/api/routes/yealink.py`
- **What**: The `yealink_alarm` docstring listed 6 numbered steps that just described what the function body does (restating the code). Four inline comments restated the code: `# Get device token`, `# Get Redis connection`, `# Create trigger service with current bucket values`, `# Handle result`.
- **Kept**: `# In simulation mode, still validate but log for debugging`, `# In simulation mode, disable rate limiting`, `# Store alarm_id in request state for logging` — all three explain WHY.
- **Fix**: Collapsed docstring to one line. Removed 4 restatement comments.

### D6: Obvious inline comments in `core/logging.py`
- **File**: `services/alarm_broker/alarm_broker/core/logging.py`
- **What**: 10 comments in `StructuredFormatter.format`, `HumanReadableFormatter.format`, and `configure_logging` restated what the immediately following line does: `# Add logger name`, `# Add extra fields`, `# Add exception info if present`, `# Add location info`, `# Base message`, `# Add exception if present`, `# Get root logger`, `# Remove existing handlers`, `# Create handler`, `# Select formatter`, `# Configure specific loggers`.
- **Kept**: `# Don't propagate to root to avoid duplicate logs` — explains WHY, not WHAT.
- **Fix**: Deleted all 11 restatement comments.

### D5: Obvious inline comments in `health.py` + "comprehensive" slop word
- **File**: `services/alarm_broker/alarm_broker/api/routes/health.py`
- **What**: `healthz_details` had a verbose multi-line docstring using "comprehensive" and three obvious section comments (`# Check database`, `# Check Redis`, `# Check connectors`) that restated exactly what the next line of code does.
- **Fix**: Collapsed the docstring to one line. Removed the three restatement comments.

## Final Review (Opus)

### F1: Defensive `getattr` on typed ZammadConfig + missed verbose module docstrings
- **File (getattr)**: `services/notification_service.py` `_build_zammad_ticket_payload`
- **What**: `getattr(zcfg, "group", "Notfallstelle")` and two similar calls used defensive attribute access against `ZammadConfig`, a frozen dataclass where these fields always exist with defaults. Same anti-pattern was fixed in Item 6 for `tasks.py` but this instance was missed.
- **Fix**: Changed to direct attribute access: `zcfg.group`, `zcfg.state_id_new`, `zcfg.customer`.
- **File (docstrings)**: `notification_service.py`, `trigger_service.py`, `worker/tasks.py`
- **What**: Three module docstrings still had the "This service/module encapsulates/contains..." verbose second-sentence pattern that was cleaned everywhere else in D10.
- **Fix**: Collapsed each to a single descriptive line.

### F4: Lint cleanup — unused imports, long lines, import ordering
- **Files**: `trigger_service.py`, `notification_service.py`, `tests/test_notes_and_simulation.py`, `tests/test_webhook_worker.py`
- **What**: Removing `_evaluate_policies` left `from typing import Any` unused and a 130-char line. A docstring exceeded 100 chars. Two test files had pre-existing unused imports (`uuid`, `pytest`). Removing `from typing import Any` broke import grouping (missing blank line between stdlib and third-party).
- **Fix**: Removed unused imports, wrapped long lines, added blank line for import sorting. All `ruff check` clean; all 49 tests pass.

### F3: Dockerfile builder stage would fail — editable install without source
- **File**: `Dockerfile`
- **What**: Builder stage copied only `pyproject.toml` to `/build/` then ran `pip install -e /build`. An editable install requires the package source directory, but only the metadata file was present. With setuptools as the build backend, this would fail with "can't find package."
- **Fix**: Copy the full `services/alarm_broker/` directory into `/build/` and use a non-editable install (`pip install /build`). This correctly resolves and installs all dependencies in the builder venv, which is then copied to the production stage.

### F2: Dead `_evaluate_policies` stub in `trigger_service.py`
- **File**: `services/trigger_service.py`
- **What**: `_evaluate_policies(device)` was a TODO stub returning a hardcoded dict. Its return value was discarded at the call site (`await self._evaluate_policies(device)` with no assignment). The method executed on every trigger for zero effect.
- **Fix**: Deleted the method and its call. Policy evaluation as a concept belongs in ROADMAP.md, not as dead code.

## GitHub & CI Audit

### G5: Remove redundant `isort` pre-commit hook (ruff handles it)
- **File**: `.pre-commit-config.yaml`
- **What**: Both `ruff` (with `I` rules = isort) and the standalone `isort` hook were running. Ruff already reformats imports; isort would then re-format them again, causing potential conflicts and double-processing.
- **Fix**: Removed the `isort` repo/hook block. Ruff's `I` rule selection handles import sorting.

### G4: Create `.secrets.baseline` and un-ignore it from `.gitignore`
- **Files**: `.secrets.baseline` (created), `.gitignore` (updated)
- **What**: `.pre-commit-config.yaml` used `detect-secrets` with `--baseline .secrets.baseline`, but the file didn't exist and was listed in `.gitignore` (so it couldn't be committed). Anyone running `pre-commit` would get a "baseline file not found" error.
- **Fix**: Created `.secrets.baseline` with empty results and the standard detect-secrets plugin/filter configuration. Removed `.secrets.baseline` from `.gitignore` so it can be tracked and shared across clones.

### G3: Fix Makefile `lint` and `audit` targets — silent no-ops
- **File**: `Makefile`
- **What (ruff no-op)**: `lint`, `lint-fix`, `format`, `format-check` all ran `cd services/alarm_broker && ruff ... services/alarm_broker`. After `cd`, the path `services/alarm_broker` resolves to `services/alarm_broker/services/alarm_broker` which doesn't exist. Ruff found no files and silently exited 0 — the lint check was a no-op.
- **What (pip-audit bug)**: `audit` ran `pip-audit services/alarm_broker` — same invalid argument as was in CI.
- **Fix**: Removed the `cd services/alarm_broker &&` prefix from ruff calls — run ruff from repo root with the correct `services/alarm_broker` path. Fixed pip-audit to `cd services/alarm_broker && pip-audit`.

### G2: Add GitHub issue templates and PR template
- **Files created**: `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/ISSUE_TEMPLATE/feature_request.md`, `.github/pull_request_template.md`
- **What**: No GitHub templates existed. Added minimal, practical bug report and feature request templates plus a PR checklist that references `make test`, `make lint`, `make audit`.

### G1: Fix `ci.yml` — broken commands, unnecessary services, wrong working directory
- **File**: `.github/workflows/ci.yml`
- **What (broken pip-audit)**: `pip-audit services/alarm_broker` is invalid — pip-audit doesn't take a directory arg. It audits the current environment; `services/alarm_broker` would be interpreted as a requirements file path and fail.
- **What (wrong pytest dir)**: `pytest -q` ran from repo root. The `pyproject.toml` with `[tool.pytest.ini_options]` (including `asyncio_mode = "auto"`) is in `services/alarm_broker/`, so pytest wouldn't pick up the config.
- **What (unnecessary services)**: The test job spun up PostgreSQL (`supercharge/postgresql-action@v1`) and Redis (`supercharge/redis-action@v1`), neither of which the tests use — `conftest.py` uses SQLite (`sqlite+aiosqlite`) and `FakeRedis`. These two unpinned third-party actions were also a supply chain risk.
- **What (push trigger)**: CI ran on push to `[main, develop]` but only `main` exists.
- **Fix**: Removed both supercharge actions and `docker/setup-buildx-action` from the test job. Changed `pytest -q` → `cd services/alarm_broker && pytest -q --tb=short`. Changed `pip-audit services/alarm_broker` → `cd services/alarm_broker && pip-audit`. Simplified push trigger to `[main]`. Reduced test job timeout from 15m to 10m (no DB spin-up needed).

### D1: README.md references four non-existent doc files
- **File**: `README.md`
- **What**: The "Main docs" list linked to `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`, `docs/DATA_MODEL.md`, and `docs/DEVELOPMENT.md` — none of which exist. The actual doc files are `SETUP.md` (covers install + dev workflow), `OPERATIONS.md` (covers troubleshooting), and `ARCHITECTURE.md` (covers data model).
- **Fix**: Replaced the four broken references with proper markdown links to the five real doc files that exist in `docs/`.
