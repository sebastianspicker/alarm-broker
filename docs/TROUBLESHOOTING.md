# Troubleshooting Guide

This guide helps diagnose and resolve common issues with Alarm Broker.

## Quick Diagnostics

Run these commands first when experiencing issues:

```bash
# Check all service status
docker compose -f deploy/docker-compose.yml ps

# Check health endpoints
curl -sS http://localhost:8080/healthz
curl -sS http://localhost:8080/readyz

# Check logs for errors
docker compose -f deploy/docker-compose.yml logs --since=5m | grep -i error
```

## API Issues

### 401 Unauthorized

**Symptom:** API returns 401 error

**Causes:**
1. Missing or incorrect `X-Admin-Key` header
2. Invalid API key in configuration

**Solutions:**

```bash
# Verify API key is set
docker compose -f deploy/docker-compose.yml exec api env | grep ADMIN_API_KEY

# Test with correct header
curl -sS -X GET "http://localhost:8080/v1/admin/devices" \
  -H "X-Admin-Key: ${ADMIN_API_KEY}"
```

### 404 Not Found

**Symptom:** Endpoint returns 404

**Solution:** Check API version prefix - endpoints require `/v1/` prefix:

```bash
# Wrong (404)
curl http://localhost:8080/admin/devices

# Correct (200)
curl http://localhost:8080/v1/admin/devices
```

### 422 Validation Error

**Symptom:** API returns validation error

**Solution:** Check request body matches schema:

```bash
# View validation error details
curl -sS -X POST "http://localhost:8080/v1/alarms" \
  -H "Content-Type: application/json" \
  -d '{"invalid": "data"}' | jq
```

## Database Issues

### Connection Refused

```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Diagnosis:**

```bash
# Check if database is running
docker compose -f deploy/docker-compose.yml ps db

# Test connection
docker compose -f deploy/docker-compose.yml exec db pg_isready -U postgres

# Check database logs
docker compose -f deploy/docker-compose.yml logs db
```

**Solutions:**

1. Wait for database to start (it may take time on first run)
2. Check DATABASE_URL format
3. Verify network connectivity between containers

### Connection Pool Exhausted

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size X overflow Y reached
```

**Diagnosis:**

```bash
# Check active connections
docker compose exec db psql -U postgres -d alarm -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname = 'alarm';"
```

**Solutions:**

1. Increase pool size in configuration
2. Check for long-running queries
3. Ensure connections are properly closed in code

### Migration Failures

```
alembic.util.exc.CommandError: Can't locate revision
```

**Solution:**

```bash
# Check current migration state
docker compose exec api alembic current

# Verify migrations exist
ls -la services/alarm_broker/alembic/versions/
```

## Redis Issues

### Connection Error

```
redis.exceptions.ConnectionError: Error connecting to Redis
```

**Diagnosis:**

```bash
# Check if Redis is running
docker compose -f deploy/docker-compose.yml ps redis

# Test connection
docker compose exec redis redis-cli ping

# Check Redis logs
docker compose logs redis
```

**Solutions:**

1. Verify REDIS_URL format
2. Check Redis is not in readonly mode
3. Ensure network connectivity

### Memory Issues

```
OOM command not allowed when used memory > 'maxmemory'
```

**Solution:**

```yaml
# docker-compose.yml
services:
  redis:
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
```

## Alarm Processing Issues

### Alarms Not Being Processed

**Diagnosis:**

```bash
# Check worker logs
docker compose logs worker

# Check for queued jobs
docker compose exec redis redis-cli LLEN arq:queue
```

**Solutions:**

1. Ensure worker is running: `docker compose up -d worker`
2. Check worker is connected to Redis
3. Verify alarm service is functioning

### Notifications Not Sending

**Diagnosis:**

```bash
# Check notification service logs
docker compose logs worker | grep -i notification
```

**Solutions:**

1. Verify connector configuration (SendXMS, Signal, etc.)
2. Check external service availability
3. Review notification retry logs

### Webhook Failures

**Diagnosis:**

```bash
# Check webhook logs
docker compose logs worker | grep -i webhook

# Check metrics
curl -sS http://localhost:8080/metrics | grep webhook
```

**Solutions:**

1. Verify webhook URL is reachable
2. Check webhook timeout settings
3. Review webhook retry configuration
4. Check target service authentication

## Performance Issues

### High Response Time

**Diagnosis:**

```bash
# Check current load
docker stats

# Check slow queries
docker compose exec db psql -U postgres -d alarm -c \
  "SELECT query, mean_time FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10;"
```

**Solutions:**

1. Add database indexes on frequently queried columns
2. Increase worker concurrency
3. Enable connection pooling
4. Check for N+1 query patterns

### High Memory Usage

**Diagnosis:**

```bash
# Check container memory
docker stats --no-stream

# Check Redis memory
docker compose exec redis redis-cli INFO memory
```

**Solutions:**

1. Restart worker to clear memory
2. Reduce batch sizes
3. Add memory limits to Docker Compose

## Docker Issues

### Container Won't Start

```bash
# Check logs
docker compose -f deploy/docker-compose.yml logs <service>

# Check for port conflicts
lsof -i :8080
```

### Image Build Fails

```bash
# Clean build
docker compose -f deploy/docker-compose.yml build --no-cache
```

## Data Issues

### Missing Data After Migration

**Solution:** Restore from backup:

```bash
pg_restore -U alarm -h localhost -d alarm -c backup_20240101.dump
```

### Corrupted Data

If data integrity is compromised:

1. Stop all services
2. Restore from last known good backup
3. Replay any missing events from source systems

## Getting Help

If issues persist:

1. Enable debug logging: `LOG_LEVEL=DEBUG`
2. Collect full logs: `docker compose logs > debug.log`
3. Check GitHub Issues for similar problems
4. Create a new issue with reproduction steps
