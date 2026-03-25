"""Tests for alarm_broker.core.idempotency and alarm_broker.core.rate_limit."""

from __future__ import annotations

import hashlib
import time

import pytest

from alarm_broker.core.idempotency import bucket_10s, idempotency_key
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key

pytestmark = [pytest.mark.unit]


# ── bucket_10s ──────────────────────────────────────────────────────────


class TestBucket10s:
    def test_explicit_epoch(self):
        """bucket_10s with an explicit epoch returns epoch // 60."""
        assert bucket_10s(120) == 2
        assert bucket_10s(179) == 2
        assert bucket_10s(180) == 3

    def test_zero_epoch(self):
        assert bucket_10s(0) == 0

    def test_none_uses_current_time(self):
        """When called without argument, bucket_10s uses time.time()."""
        expected = int(time.time()) // 60
        result = bucket_10s()
        # Allow a 1-bucket tolerance in case the second ticks over
        assert abs(result - expected) <= 1

    def test_same_60s_window_same_bucket(self):
        """Two timestamps within the same 60-second window produce the same bucket."""
        assert bucket_10s(1020) == bucket_10s(1079)

    def test_different_60s_window_different_bucket(self):
        """Timestamps in different 60-second windows produce different buckets."""
        assert bucket_10s(1020) != bucket_10s(1080)


# ── idempotency_key ────────────────────────────────────────────────────


class TestIdempotencyKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = idempotency_key("abc", 42)
        key2 = idempotency_key("abc", 42)
        assert key1 == key2

    def test_matches_sha256(self):
        """The key is the sha256 hex digest of '{token}:{bucket}'."""
        token, bucket = "my-token", 99
        expected = hashlib.sha256(f"{token}:{bucket}".encode()).hexdigest()
        assert idempotency_key(token, bucket) == expected

    def test_different_tokens_different_keys(self):
        """Different tokens produce different keys."""
        assert idempotency_key("a", 1) != idempotency_key("b", 1)

    def test_different_buckets_different_keys(self):
        """Different buckets produce different keys."""
        assert idempotency_key("a", 1) != idempotency_key("a", 2)

    def test_key_is_hex_string(self):
        """The result is a 64-character hex string (sha256)."""
        key = idempotency_key("x", 0)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ── minute_bucket ──────────────────────────────────────────────────────


class TestMinuteBucket:
    def test_explicit_epoch(self):
        """minute_bucket with an explicit epoch returns epoch // 60."""
        assert minute_bucket(120) == 2
        assert minute_bucket(179) == 2
        assert minute_bucket(180) == 3

    def test_zero_epoch(self):
        assert minute_bucket(0) == 0

    def test_none_uses_current_time(self):
        """When called without argument, minute_bucket uses time.time()."""
        expected = int(time.time()) // 60
        result = minute_bucket()
        assert abs(result - expected) <= 1

    def test_same_minute_same_bucket(self):
        assert minute_bucket(600) == minute_bucket(659)

    def test_different_minute_different_bucket(self):
        assert minute_bucket(600) != minute_bucket(660)


# ── rate_limit_key ─────────────────────────────────────────────────────


class TestRateLimitKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = rate_limit_key("tok", 10)
        key2 = rate_limit_key("tok", 10)
        assert key1 == key2

    def test_format(self):
        """The key has the format 'rl:{sha256(token)}:{bucket}'."""
        token, bucket = "my-token", 5
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expected = f"rl:{token_hash}:{bucket}"
        assert rate_limit_key(token, bucket) == expected

    def test_different_tokens_different_keys(self):
        assert rate_limit_key("a", 1) != rate_limit_key("b", 1)

    def test_different_buckets_different_keys(self):
        assert rate_limit_key("a", 1) != rate_limit_key("a", 2)

    def test_token_is_hashed(self):
        """The raw token should not appear in the key (it's hashed)."""
        token = "secret-device-token"
        key = rate_limit_key(token, 0)
        assert token not in key
        assert key.startswith("rl:")
