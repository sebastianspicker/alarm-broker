# Demo Runbook: Mock University Screenshots

This runbook creates a fully local, reproducible demo flow and captures 10 screenshots.

## Prerequisites

1. Docker Desktop is running.
2. `.env` exists and includes:
   - `ADMIN_API_KEY=<your-key>`
   - `SIMULATION_ENABLED=true`
3. Services are started:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

4. Migrations are applied:

```bash
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
```

5. Optional but recommended for screenshot capture:

```bash
pip install playwright
playwright install chromium
```

## Demo Data

The canonical demo dataset is in `deploy/simulation_seed.yaml` and models a product-like "Mock University" setup:

- Sites: `uni-north`, `uni-med`
- Rooms: Security Ops, Library Desk, Chemistry Lab, Surgical Unit OR, Dorm Lobby
- Roles: Security Desk, Campus Ops, Lab Supervisor, On-Call Nurse, Duty Manager
- Tokens for trigger scenes:
  - `MU_YLK_NORTH_OPS_2001`
  - `MU_YLK_NORTH_LIB_2002`
  - `MU_YLK_CHEM_LAB_2003`
  - `MU_YLK_MED_OR_2004`
  - `MU_YLK_DORM_LOBBY_2005`

## End-to-End Commands

## 1) Prepare deterministic demo baseline

```bash
python scripts/demo_prepare.py
```

What it does:
- Checks `/readyz`
- Loads `deploy/simulation_seed.yaml` via `/v1/admin/seed`
- Clears simulation notifications via `/v1/simulation/notifications/clear`

## 2) Capture the screenshot set

```bash
python scripts/demo_capture.py
```

Output directory:
- `docs/assets/screenshots`

Generated files:
1. `01-admin-overview.png`
2. `02-admin-triggered-alarm.png`
3. `03-admin-search-filter.png`
4. `04-admin-detail-modal.png`
5. `05-admin-quick-acknowledged.png`
6. `06-ack-page-triggered-mobile.png`
7. `07-ack-page-acknowledged-mobile.png`
8. `08-admin-resolved-state.png`
9. `09-simulation-feed.png`
10. `10-simulation-feed-after-clear.png`

## Script Options

`demo_prepare.py`:
- `--base-url` (default `http://localhost:8080`)
- `--admin-key` (fallback: `ADMIN_API_KEY` env var)
- `--seed-file` (default `deploy/simulation_seed.yaml`)
- `--timeout-seconds`

`demo_capture.py`:
- `--base-url`
- `--admin-key`
- `--output-dir` (default `docs/assets/screenshots`)
- `--seed-file`
- `--timeout-seconds`
- `--wait-seconds`
- `--headed` (run browser visible)
- `--skip-prepare`
- `--mock-screens` (creates placeholders without browser/server)

## Troubleshooting

## 401 Unauthorized
- Cause: wrong or missing `ADMIN_API_KEY`.
- Action: export correct key or pass `--admin-key`.

## 404 on `/v1/simulation/*`
- Cause: simulation mode disabled.
- Action: set `SIMULATION_ENABLED=true` in `.env` and restart stack.

## No simulation notifications in screenshot run
- Cause: worker is not running or not processing jobs.
- Action:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs worker --tail=200
```

## Playwright not installed
- Action:

```bash
pip install playwright
playwright install chromium
```
