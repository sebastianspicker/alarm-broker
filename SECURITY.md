# Security Policy

## Supported Versions

We release patches for security vulnerabilities. The following versions are currently supported:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it by creating a GitHub Security Advisory. We appreciate responsible disclosure and will work with you to resolve the issue.

## Security Features

### Authentication

- Admin API endpoints require a secure API key (`X-Admin-Key` header)
- Admin key must be configured via `ADMIN_API_KEY` environment variable
- Empty admin key results in fail-closed behavior (500 error)

### Rate Limiting

- Configurable rate limiting per device token (default: 10 requests/minute)
- Redis-based rate limiting for distributed systems

### IP Allowlisting

- Yealink endpoints support IP allowlisting
- Configurable via `YELK_IP_ALLOWLIST` environment variable

### Trusted Proxy

- Support for X-Forwarded-For header validation
- Configurable trusted proxy CIDRs to prevent IP spoofing

### Input Validation

- Pydantic-based request validation
- SQLAlchemy ORM for database queries (prevents SQL injection)
- Parameterized queries

### Security Headers

- CORS configuration available
- Security headers can be added via middleware

## Best Practices

1. **Use HTTPS in production** - Configure a reverse proxy with TLS
2. **Generate strong API keys** - Use random keys with sufficient entropy:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. **Restrict network access** - Use firewall rules to limit access to necessary IPs
4. **Regular updates** - Keep dependencies up to date
5. **Monitor logs** - Watch for unusual activity
6. **Backup regularly** - Maintain database backups

## Dependencies Security

We use `pip-audit` in CI to check for known vulnerabilities in dependencies.

```bash
pip-audit services/alarm_broker
```
