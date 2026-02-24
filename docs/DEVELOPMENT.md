# Development Guide

This guide covers local development setup, testing, and contribution guidelines for the alarm-broker project.

## Prerequisites

- Docker Desktop
- Python 3.12+ (optional for local dev; Docker is enough to run)
- Make (optional, for convenience commands)

## Quick Start

### 1. Create Environment File

```bash
cp .env.example .env
```

Edit `.env` and configure at minimum:
- `ADMIN_API_KEY` - secure admin API key
- `ZAMMAD_API_TOKEN` - if using Zammad integration
- `SENDXMS_API_KEY` - if using SMS integration

### 2. Start Services

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

### 3. Run Database Migrations

```bash
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
```

### 4. Load Seed Data

```bash
curl -sS -X POST "http://localhost:8080/v1/admin/seed" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @deploy/seed.example.yaml
```

### 5. Verify Installation

```bash
curl -sS "http://localhost:8080/readyz" | jq .
```

## Development Workflow

### Running Tests

```bash
make test
# or directly:
./.venv/bin/pytest -q
```

### Code Quality

```bash
# Format and lint check
make lint

# Security audit
make audit
```

### Cleaning Up

```bash
make clean
```

## Project Structure

```
alarm-broker/
├── deploy/                    # Docker Compose and seed examples
├── docs/                      # Documentation
│   ├── ARCHITECTURE.md        # System architecture
│   ├── DATA_MODEL.md          # Database schema
│   ├── INTEGRATIONS.md        # External integrations
│   └── DEVELOPMENT.md         # This file
├── services/
│   └── alarm_broker/
│       ├── alarm_broker/      # Main application package
│       │   ├── api/           # FastAPI routes and schemas
│       │   ├── connectors/    # External service clients
│       │   ├── core/          # Core utilities
│       │   ├── db/            # Database models and migrations
│       │   ├── services/      # Business logic layer
│       │   └── worker/        # Background task workers
│       ├── tests/             # Test suite
│       └── pyproject.toml     # Python dependencies
├── plans/                     # Improvement plans
└── Makefile                   # Development commands
```

## API Endpoints

### Public Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Basic liveness check |
| `/readyz` | GET | Readiness check (DB + Redis) |
| `/v1/yealink/alarm` | GET | Yealink alarm trigger |
| `/a/{ack_token}` | GET/POST | Alarm acknowledgment UI |

### Admin Endpoints (require `X-Admin-Key` header)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/alarms` | GET | List alarms (paginated) |
| `/v1/alarms/{id}` | GET | Get alarm details |
| `/v1/alarms/{id}/ack` | POST | Acknowledge alarm |
| `/v1/alarms/{id}/resolve` | POST | Resolve alarm |
| `/v1/alarms/{id}/cancel` | POST | Cancel alarm |
| `/v1/admin/seed` | POST | Load seed data |
| `/v1/admin/devices` | POST/PUT | Upsert device |
| `/v1/admin/escalation-policy` | POST | Configure escalation |

## Configuration

All configuration is via environment variables. See `.env.example` for available options.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `BASE_URL` | `http://localhost:8080` | Public base URL for ACK links |
| `LOG_LEVEL` | `INFO` | Logging level |

### Security Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_API_KEY` | *(empty)* | Admin API authentication key |
| `ENABLE_API_DOCS` | `false` | Enable `/docs` and `/redoc` endpoints |
| `YELK_IP_ALLOWLIST` | *(empty)* | Comma-separated IPs/CIDRs for Yealink |
| `TRUSTED_PROXY_CIDRS` | *(empty)* | Trusted proxy CIDRs for X-Forwarded-For |
| `RATE_LIMIT_PER_MINUTE` | `10` | Rate limit per device token |

### Integration Settings

See `.env.example` for Zammad, SMS, and Signal configuration options.

## Database Migrations

### Create a New Migration

```bash
docker compose -f deploy/docker-compose.yml exec api alembic revision --autogenerate -m "description"
```

### Apply Migrations

```bash
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
```

### Rollback

```bash
docker compose -f deploy/docker-compose.yml exec api alembic downgrade -1
```

## Testing

### Test Structure

- `tests/test_api_flow.py` - API endpoint tests
- `tests/test_lifecycle_and_ops.py` - Alarm lifecycle tests
- `tests/test_security_hardening.py` - Security regression tests

### Running Specific Tests

```bash
# Run security tests only
./.venv/bin/pytest -q -m security

# Run with verbose output
./.venv/bin/pytest -v

# Run specific test file
./.venv/bin/pytest tests/test_api_flow.py
```

## Troubleshooting

### Database Connection Issues

1. Verify PostgreSQL is running: `docker compose ps`
2. Check connection string in `.env`
3. Ensure migrations are applied: `alembic current`

### Redis Connection Issues

1. Verify Redis is running: `docker compose ps`
2. Check Redis URL in `.env`
3. Test connection: `redis-cli ping`

### Worker Not Processing Jobs

1. Check worker logs: `docker compose logs worker`
2. Verify Redis connection
3. Check for stuck jobs in Redis

## Contributing

1. Create a feature branch
2. Make changes with tests
3. Run `make lint test audit`
4. Submit pull request

## Security

See `docs/archive/DEEP_CODE_INSPECTION_FINDINGS.md` for historical security findings and fixes.

### Reporting Security Issues

Please report security vulnerabilities privately to the maintainers.
