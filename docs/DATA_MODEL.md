# Data Model (PostgreSQL)

Core tables and responsibilities.

## Master data

- `sites`
- `rooms` (FK -> `sites`)
- `persons`
- `devices` (token mapping to person/room)

`devices.device_token` is the inbound trigger anchor.

## Escalation configuration

- `escalation_targets`
- `escalation_policy`
- `escalation_steps` (composite PK: `policy_id`, `step_no`, `target_id`)

Validation rules enforced in API layer include:
- no duplicate target IDs inside the same step,
- no duplicate `(step_no, target_id)` pairs,
- all referenced target IDs must exist (incoming or already persisted).

## Alarm state and audit

### `alarms`

Main incident table.

Important fields:
- identity/timing:
  - `id` (UUID PK)
  - `created_at`
- status lifecycle:
  - `status` (`triggered`, `acknowledged`, `resolved`, `cancelled`)
  - `acked_at`, `acked_by`
  - `resolved_at`, `resolved_by`
  - `cancelled_at`, `cancelled_by`
- context:
  - `person_id`, `room_id`, `site_id`, `device_id`
  - `source`, `event`, `severity`, `silent`
- integration fields:
  - `ack_token` (capability URL token)
  - `zammad_ticket_id`
- metadata:
  - `meta` JSON

### `alarm_notifications`

Audit stream for outbound connector attempts.

Tracks:
- `channel`, `target_id`, `payload`
- `result` (`ok`/`error`)
- `error` text
- `created_at`

## Migration history

- `0001_initial_schema`
- `0002_alarm_lifecycle_fields` (resolve/cancel columns)

## Query patterns

- Recent alarm listing:
  - ordered by `created_at DESC, id DESC`
  - optional cursor pagination by last seen alarm ID
- Lookup by ACK token:
  - `ack_token` unique index/constraint
- Device trigger lookup:
  - `device_token` unique index/constraint
