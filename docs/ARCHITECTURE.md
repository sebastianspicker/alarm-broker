# Alarm Broker Architecture (PoC + Hardening)

This repository implements a PoC alarm broker that receives silent alarms and fans out notifications across multiple channels while persisting an auditable lifecycle.

## Scope and intent

The system is intentionally designed as a hardened PoC:
- stable core flow,
- clear auditability,
- secure defaults,
- additive, backward-compatible API evolution.

It is not a complete production platform yet.

## Runtime components

- FastAPI API service (`alarm_broker.api`)
- arq worker (`alarm_broker.worker`)
- PostgreSQL (state + audit)
- Redis (idempotency, rate limiting, jobs)
- Connector clients:
  - Zammad
  - SMS provider (generic HTTP)
  - Signal endpoint (signal-cli-rest-api compatible)

## End-to-end flow

1. Trigger source (Yealink webhook) calls:
- `GET /v1/yealink/alarm?token=<device_token>`

2. API validates:
- source IP allowlist,
- idempotency bucket,
- per-token rate limits,
- device mapping consistency.

3. API persists `alarms` row with `status=triggered`, `ack_token`, metadata, then enqueues `alarm_created`.

4. Worker enriches alarm context (person/room/site), sends stage 0 notifications, and schedules escalation jobs.

5. ACK flow:
- `GET /a/{ack_token}` renders responder page,
- `POST /a/{ack_token}` acknowledges alarm and enqueues `alarm_acked`.

6. Admin lifecycle/API:
- `POST /v1/alarms/{alarm_id}/ack`
- `POST /v1/alarms/{alarm_id}/resolve`
- `POST /v1/alarms/{alarm_id}/cancel`

## Alarm lifecycle

Allowed transitions:
- `triggered -> acknowledged`
- `triggered -> resolved`
- `triggered -> cancelled`
- `acknowledged -> resolved`
- `acknowledged -> cancelled`

Invalid transitions are rejected with `409`.
Repeated transition to same target state is idempotent (`204`).

## Operational endpoints

- `GET /healthz`: basic liveness
- `GET /readyz`: DB + Redis readiness

`/readyz` returns `503` if one dependency is unavailable.

## API notes

- Existing endpoints remain backward-compatible.
- `GET /v1/alarms` supports additive pagination via optional `cursor` + `limit`.
- If another page exists, header `X-Next-Cursor` is returned.

## Observability baseline

A request middleware adds:
- request ID (`X-Request-ID` response header),
- structured request logs with route, status, latency, and optional alarm ID.

## Security baseline

- Fail-closed admin auth when `ADMIN_API_KEY` is unset.
- Token and IP validation on inbound trigger routes.
- Escaped ACK HTML rendering.
- Robust input parsing/validation for admin seed and escalation policy operations.
