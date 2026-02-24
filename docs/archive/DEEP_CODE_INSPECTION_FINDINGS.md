# Deep Code Inspection Findings

Date: 2026-02-24
Scope: `services/alarm_broker`
Status: all identified P0-P3 findings fixed.

## Findings (prioritized by likelihood)

1. P1 - IP allowlist bypass via untrusted `X-Forwarded-For` header
- Why it could happen: client-provided `X-Forwarded-For` was trusted unconditionally, so attackers could spoof an allowed source IP.
- Fixed at: `alarm_broker/api/deps.py` (`get_client_ip` now only trusts forwarded headers from configured trusted proxy CIDRs).
- Regression test: `test_untrusted_x_forwarded_for_does_not_bypass_ip_allowlist`, `test_trusted_proxy_allows_forwarded_client_ip`.

2. P1 - Stored/reflected XSS risk in ACK HTML page
- Why it could happen: user/admin-controlled values (person/room/status/time) were inserted into HTML without escaping.
- Fixed at: `alarm_broker/services/ack_ui.py` + `alarm_broker/api/routes/ack.py` (template-based ACK page with escaped values).
- Regression test: `test_ack_page_escapes_untrusted_html`.

3. P2 - Sensitive device token exposure in Redis rate-limit key
- Why it could happen: raw `device_token` was embedded in Redis key names and could leak through logs/metrics.
- Fixed at: `alarm_broker/core/rate_limit.py` (rate-limit key now uses SHA-256 hash of token).
- Regression test: `test_rate_limit_key_does_not_include_raw_token`.

4. P2 - API schema/docs exposed by default
- Why it could happen: FastAPI default docs/OpenAPI endpoints were enabled in production-like runs.
- Fixed at: `alarm_broker/api/main.py` + `alarm_broker/settings.py` (`ENABLE_API_DOCS=false` default, docs explicitly opt-in).
- Regression test: `test_docs_and_openapi_disabled_by_default`.

5. P2 - Insecure default admin key
- Why it could happen: default `ADMIN_API_KEY` was a known static value (`dev-admin-key`), allowing unauthorized admin access if env setup was missed.
- Fixed at: `alarm_broker/settings.py` (default now empty; endpoint fails closed if not configured).
- Regression test: `test_default_admin_api_key_is_empty`.

6. P3 - Non-constant-time admin key comparison
- Why it could happen: direct string comparison can leak timing information in edge scenarios.
- Fixed at: `alarm_broker/api/deps.py` (`secrets.compare_digest` used).

7. P1 - Invalid alarm ID path could trigger 500 errors
- Why it could happen: admin alarm routes accepted `alarm_id` as free-form string; invalid UUID values reached SQLAlchemy UUID binders and crashed with `StatementError`.
- Fixed at: `alarm_broker/api/routes/alarms.py` (path params typed as `uuid.UUID`; invalid values rejected with 422).
- Regression test: `test_invalid_alarm_id_rejected_with_422`.

8. P1 - Invalid allowlist CIDR could crash request handling
- Why it could happen: malformed `YELK_IP_ALLOWLIST` entries were parsed without error handling, raising `ValueError` and causing 500 responses.
- Fixed at: `alarm_broker/core/ip_allowlist.py` (`ip_allowed` now catches parsing errors and fails closed).
- Regression test: `test_invalid_allowlist_config_fails_closed_without_500`.

9. P2 - Invalid trusted proxy CIDR could crash request handling
- Why it could happen: malformed `TRUSTED_PROXY_CIDRS` entries raised `ValueError` during header trust checks.
- Fixed at: `alarm_broker/api/deps.py` (invalid CIDRs are ignored; no trusted proxy is assumed for bad entries).
- Regression test: `test_invalid_trusted_proxy_config_is_ignored_without_500`.

10. P2 - Insecure admin key value in shipped `.env.example`
- Why it could happen: example file used a known static secret (`dev-admin-key`), encouraging accidental insecure deployments.
- Fixed at: `.env.example` and `README.md` (placeholder key changed to `change-me-admin-key`).
- Regression test: `test_env_example_does_not_ship_static_admin_secret`.

11. P1 - Invalid JSON/YAML in admin seed endpoint could trigger 500 errors
- Why it could happen: parser exceptions (`JSONDecodeError`, `YAMLError`) were not handled and bubbled up as server errors.
- Fixed at: `alarm_broker/services/seed_service.py` + `alarm_broker/api/routes/admin.py` (`/v1/admin/seed` returns 400 on parse errors).
- Regression test: `test_admin_seed_invalid_json_returns_400`, `test_admin_seed_invalid_yaml_returns_400`.

12. P2 - `application/yaml` seed content-type was not supported
- Why it could happen: endpoint only accepted a subset of YAML content types, causing valid YAML payloads to be parsed as JSON and fail.
- Fixed at: `alarm_broker/services/seed_service.py` (supports `application/yaml` and `application/yml`).
- Regression test: `test_admin_seed_accepts_application_yaml_content_type`.

13. P1 - Escalation policy accepted missing target references
- Why it could happen: step `target_ids` were not validated against incoming/existing targets, allowing inconsistent policy data and runtime failures.
- Fixed at: `alarm_broker/services/policy_service.py` + `alarm_broker/api/routes/admin.py` (unknown target IDs rejected with 400).
- Regression test: `test_policy_rejects_missing_target_references`.

14. P2 - Insecure Zammad token defaults could cause accidental outbound calls
- Why it could happen: default token values (`change-me`) made connector look configured, risking unintended external requests with alarm metadata.
- Fixed at: `alarm_broker/settings.py` and `.env.example` (default/placeholder token now empty, so connector stays disabled until explicitly configured).
- Regression test: `test_default_zammad_api_token_is_empty`, `test_env_example_does_not_ship_static_zammad_token`.

15. P1 - IPv6 host entries in allowlist matched an unintended /32 network
- Why it could happen: host entries without CIDR were always normalized as `/32`; for IPv6 this expands to a broad network and can unintentionally allow extra clients.
- Fixed at: `alarm_broker/core/ip_allowlist.py` (host entries now use `/32` for IPv4 and `/128` for IPv6).
- Regression test: `test_ip_allowlist_ipv6_host_entry_matches_only_exact_host`.

16. P1 - Duplicate escalation step targets could trigger DB integrity 500
- Why it could happen: duplicate `(step_no, target_id)` pairs were not validated before insert, leading to unique-constraint failures on commit.
- Fixed at: `alarm_broker/services/policy_service.py` + `alarm_broker/api/routes/admin.py` (duplicates rejected with 400 before DB write).
- Regression test: `test_policy_duplicate_step_target_rejected`.

17. P1 - Structurally invalid seed payloads could trigger 500
- Why it could happen: missing required keys in nested seed objects (for example `sites: [{}]`) raised `KeyError` during `apply_seed`.
- Fixed at: `alarm_broker/services/seed_service.py` + `alarm_broker/api/routes/admin.py` (invalid seed structure returns 400 after rollback).
- Regression test: `test_admin_seed_invalid_structure_returns_400`.

18. P2 - Environment boolean expansion in seed logic was unsafe
- Why it could happen: `${VAR}` values like `false` were treated as truthy strings by `bool(...)`, silently flipping intended booleans.
- Fixed at: `alarm_broker/seed.py` (explicit boolean coercion for env expansion and seed bool fields).
- Regression test: `test_seed_env_false_expands_to_boolean_false`.

19. P1 - ACK capability URLs were cacheable and could leak via browser/proxy caches
- Why it could happen: `/a/{ack_token}` contains a bearer-style token in the path, but responses had no explicit anti-caching headers.
- Fixed at: `alarm_broker/api/main.py` (security middleware adds `Cache-Control: no-store` and `Pragma: no-cache` for `/a/*` routes).
- Regression test: `test_ack_page_sets_no_store_and_security_headers`.

20. P2 - ACK/Lifecycle free-text inputs were unbounded
- Why it could happen: `acked_by`, `actor`, and `note` fields accepted arbitrary length input, enabling storage abuse and oversized payload handling costs.
- Fixed at: `alarm_broker/api/schemas.py` + `alarm_broker/api/routes/ack.py` (max-length validation for ACK/lifecycle inputs, including form POST validation path).
- Regression test: `test_ack_form_rejects_oversized_note`, `test_alarm_transition_rejects_oversized_actor`.

## Validation

- `pytest -q` -> 32 passed
- `ruff check services/alarm_broker` -> all checks passed
- `bandit -q -r services/alarm_broker/alarm_broker` -> no findings
- `pip-audit services/alarm_broker` -> no known vulnerabilities
