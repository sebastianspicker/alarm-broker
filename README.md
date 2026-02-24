# alarm-broker (MVP / Proof of Concept)

## DISCLAIMER (PLEASE READ)

This project is **work in progress** and currently **not a production-ready alarm broker**.

- **Do not use this repository “as-is” in safety-critical, security-critical, or compliance-critical environments.**
- **Do not rely on it for emergency response, duress alarms, personal safety, or life safety use cases.**
- It is an **open-source proof of concept** intended to explore a reference architecture, integration patterns, and an MVP workflow.
- No warranty is provided; you are responsible for risk assessment, hardening, monitoring, redundancy, and operational procedures.

## What this is

An Alarm Broker MVP that receives a silent/panic alarm trigger (e.g. Yealink emergency key) and fans out notifications to:

- Zammad (create/update ticket)
- SMS (generic HTTP connector placeholder)
- Signal (expects a signal-cli-rest-api compatible endpoint)

It supports:
- Persisting alarms in PostgreSQL (with audit logs)
- An ACK (acknowledge) capability link (`/a/{ack_token}`)
- A simple escalation schedule via Redis-backed jobs
- Prometheus-style metrics (`/metrics`)
- A read-only admin dashboard (`/admin?key=<ADMIN_API_KEY>`)

## Flow diagrams (Mermaid)

The diagrams below reflect the flow as implemented in this repository.

### 1) System overview (runtime components)

```mermaid
flowchart LR
  %% External trigger/source
  Y["Yealink phone<br/>(Emergency key)"] -->|"HTTP GET /v1/yealink/alarm?token=..."| API["Alarm Broker API<br/>(FastAPI)"]

  %% Core state & job infrastructure
  API -->|"INSERT/UPDATE"| PG["PostgreSQL<br/>(alarms, mapping, audit)"]
  API -->|"SET idempotency key (NX, EX)"| R["Redis<br/>(idempotency, rate limit, jobs)"]
  API -->|"INCR rate-limit key"| R
  API -->|"enqueue_job('alarm_created')"| R

  %% Worker fan-out & escalation
  R -->|"arq jobs"| W["Alarm Worker<br/>(arq)"]
  W -->|"SELECT alarm + enrichment"| PG
  W -->|"INSERT audit rows"| PG
  W -->|"enqueue_job('escalate', _defer_by=...)"| R

  %% Downstream connectors (best effort)
  W -->|"create ticket / add note"| Z["Zammad API"]
  W -->|"send message"| SMS["SMS provider<br/>(generic HTTP connector)"]
  W -->|"send message"| SIG["Signal endpoint<br/>(signal-cli-rest-api)"]

  %% Responder acknowledgement flow
  RESP["Responder<br/>(web browser)"] -->|"GET/POST /a/{ack_token}"| API
  API -->|"enqueue_job('alarm_acked')"| R
  W -->|"Zammad internal note (ACK)"| Z

  %% Admin flow (seeding/mapping)
  ADMIN["Admin (operator)"] -->|"X-Admin-Key /v1/admin/seed"| API
  ADMIN -->|"X-Admin-Key /v1/admin/devices"| API
  ADMIN -->|"X-Admin-Key /v1/admin/escalation-policy"| API
```

### 2) Trigger flow (Yealink → API → DB → worker)

```mermaid
sequenceDiagram
  autonumber
  participant Y as Yealink phone
  participant API as Alarm Broker API (FastAPI)
  participant R as Redis
  participant PG as PostgreSQL
  participant W as Alarm Worker (arq)
  participant Z as Zammad
  participant SMS as SMS provider
  participant SIG as Signal endpoint

  Y->>API: GET /v1/yealink/alarm?token=DEVICE_TOKEN
  API->>R: GET idemp:sha256(token:bucket_10s)
  alt idempotency key exists
    R-->>API: alarm_id (existing)
    API->>PG: SELECT alarms.id (by alarm_id)
    API-->>Y: 200 {alarm_id, status}
  else first request in bucket
    API->>R: SET idemp:* = alarm_uuid NX EX 30
    API->>R: INCR rl:token:minute_bucket (+ EXPIRE)
    alt rate limit exceeded
      API->>R: DEL idemp:*
      API-->>Y: 429 Rate limit exceeded
    else allowed
      API->>PG: SELECT devices by device_token
      alt unknown token
        API->>R: DEL idemp:*
        API-->>Y: 404 Unknown token
      else mapping incomplete
        API->>R: DEL idemp:*
        API-->>Y: 409 Mapping incomplete
      else ok
        API->>PG: INSERT alarms(status=triggered, ack_token, meta, ...)
        API->>PG: UPDATE devices.last_seen_at
        API->>R: enqueue_job("alarm_created", alarm_id)
        API-->>Y: 200 {alarm_id, status:"triggered"}
      end
    end
  end

  %% async fan-out
  R-->>W: alarm_created(alarm_id)
  W->>PG: SELECT alarm + enrichment (person/room/site)
  W->>Z: POST /api/v1/tickets (best effort)
  W->>SMS: send message (best effort)
  W->>SIG: send message (best effort)
  W->>PG: INSERT alarm_notifications (audit)
  W->>R: enqueue_job("escalate", alarm_id, step_no, _defer_by=after_seconds)
```

### 3) Escalation loop (delayed jobs)

```mermaid
sequenceDiagram
  autonumber
  participant R as Redis
  participant W as Alarm Worker (arq)
  participant PG as PostgreSQL
  participant SMS as SMS provider
  participant SIG as Signal endpoint

  R-->>W: escalate(alarm_id, step_no) after delay
  W->>PG: SELECT alarms.status
  alt status != triggered
    W-->>R: (no-op)
  else status == triggered
    W->>PG: SELECT escalation_steps(step_no) + targets
    W->>SMS: send message (best effort)
    W->>SIG: send message (best effort)
    W->>PG: INSERT alarm_notifications (audit)
  end
```

### 4) ACK flow (capability link)

```mermaid
sequenceDiagram
  autonumber
  participant U as Responder (browser)
  participant API as Alarm Broker API (FastAPI)
  participant PG as PostgreSQL
  participant R as Redis
  participant W as Alarm Worker (arq)
  participant Z as Zammad

  U->>API: GET /a/{ack_token}
  API->>PG: SELECT alarms by ack_token
  API-->>U: HTML page ("Acknowledge" button)

  U->>API: POST /a/{ack_token} (acked_by?, note?)
  API->>PG: UPDATE alarms.status=acknowledged, acked_at, acked_by, meta.ack_note
  API->>R: enqueue_job("alarm_acked", alarm_id, acked_by, note)
  API-->>U: HTML page (already acknowledged)

  R-->>W: alarm_acked(alarm_id, acked_by, note)
  W->>PG: SELECT alarms.zammad_ticket_id
  W->>Z: PUT /api/v1/tickets/{id} (internal note, best effort)
  W->>PG: INSERT alarm_notifications (audit)
```

### 5) Alarm lifecycle (current implementation)

```mermaid
stateDiagram-v2
  [*] --> triggered
  triggered --> acknowledged: ACK (/a/{ack_token} or admin API)
  triggered --> resolved: Resolve API
  triggered --> cancelled: Cancel API
  acknowledged --> resolved: Resolve API
  acknowledged --> cancelled: Cancel API
  resolved --> [*]
  cancelled --> [*]
```

## Repository layout

- `docs/` – concepts and specifications (English)
- `services/alarm_broker/` – FastAPI API + arq worker + Alembic migrations
- `deploy/` – Docker Compose + example seed file

Main docs:
- `docs/ARCHITECTURE.md`
- `docs/DATA_MODEL.md`
- `docs/INTEGRATIONS.md`
- `docs/archive/DEEP_CODE_INSPECTION_FINDINGS.md`

## Requirements

- Docker Desktop
- Python 3.12+ (optional for local dev; Docker is enough to run)

## Quickstart (Docker, local)

1) Create `.env`:

```bash
cp .env.example .env
```

2) Start services:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

3) Run DB migrations:

```bash
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
```

4) Load seeds (example):

```bash
curl -sS -X POST "http://localhost:8080/v1/admin/seed" \
  -H "X-Admin-Key: change-me-admin-key" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @deploy/seed.example.yaml
```

5) Smoke test (Yealink trigger):

```bash
curl -sS "http://localhost:8080/v1/yealink/alarm?token=YLK_T54W_3F9A" | jq .
```

The response contains `alarm_id`. Fetch the alarm details (admin key required) to get `ack_token`:

```bash
curl -sS "http://localhost:8080/v1/alarms/<alarm_id>" -H "X-Admin-Key: change-me-admin-key" | jq .
```

Then open the ACK page:

```bash
open "http://localhost:8080/a/<ack_token>"
```

Readiness check:

```bash
curl -sS "http://localhost:8080/readyz" | jq .
```

## Configuration

See `.env.example` for available variables (Zammad, SMS, Signal, escalation).

Notes:
- The SMS connector is intentionally generic and expects an HTTP endpoint (see `SENDXMS_*` variables).
- Signal expects a `signal-cli-rest-api` compatible endpoint.

## Developer workflow (local)

```bash
make lint
make test
make audit
```
