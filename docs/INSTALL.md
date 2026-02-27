# Installation Guide

This guide covers the installation of Alarm Broker in production environments.

## Prerequisites

- Docker & Docker Compose
- PostgreSQL 14+
- Redis 7+
- Python 3.12+ (for local development)

## Quick Start (Docker Compose)

### 1. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` and configure at minimum:

```bash
# REQUIRED: Set a secure admin API key
ADMIN_API_KEY=your-secure-random-key-here

# REQUIRED: Configure database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/alarm

# REQUIRED: Configure Redis
REDIS_URL=redis://host:6379/0

# REQUIRED: Public base URL for ACK links
BASE_URL=https://alarm.yourdomain.com
```

### 2. Start Services

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

### 3. Run Migrations

```bash
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
```

### 4. Load Initial Data

```bash
curl -sS -X POST "http://localhost:8080/v1/admin/seed" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @deploy/seed.example.yaml
```

### 5. Verify Installation

```bash
# Health check
curl -sS http://localhost:8080/healthz

# Readiness check
curl -sS http://localhost:8080/readyz
```

## Production Installation

### Database Setup

```sql
-- Create dedicated database
CREATE DATABASE alarm;

-- Create user
CREATE USER alarm WITH PASSWORD 'secure-password';
GRANT ALL PRIVILEGES ON DATABASE alarm TO alarm;
GRANT ALL ON SCHEMA public TO alarm;
```

### Redis Configuration

For production, use Redis with persistence:

```yaml
# docker-compose.override.yml
services:
  redis:
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes
```

### Security Recommendations

1. **Use HTTPS** - Configure a reverse proxy (nginx, traefik)
2. **Firewall** - Only expose port 80/443
3. **Secrets** - Use Docker secrets or external secret management
4. **Non-root** - Run containers as non-root user

### Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name alarm.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| ADMIN_API_KEY | Yes | - | Admin API key (generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| DATABASE_URL | Yes | - | PostgreSQL connection URL |
| REDIS_URL | Yes | redis://localhost:6379/0 | Redis connection URL |
| BASE_URL | Yes | http://localhost:8080 | Public base URL for ACK links |
| LOG_LEVEL | No | INFO | Logging level |

See `.env.example` for all available configuration options.

## Upgrading

### Backup

Before upgrading, backup your database:

```bash
pg_dump -U alarm -h localhost alarm > backup_$(date +%Y%m%d).sql
```

### Update Steps

```bash
# Pull latest changes
git pull

# Rebuild containers
docker compose -f deploy/docker-compose.yml build

# Run migrations
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head

# Restart services
docker compose -f deploy/docker-compose.yml restart
```

## Uninstallation

```bash
# Stop and remove containers
docker compose -f deploy/docker-compose.yml down

# Remove volumes (WARNING: This deletes all data!)
docker compose -f deploy/docker-compose.yml down -v

# Remove images
docker compose -f deploy/docker-compose.yml rmi
```
