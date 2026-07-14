"""Tests for alarm_broker/worker/tasks.py — worker task dispatch, webhooks, escalation."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import respx
from sqlalchemy import select

from alarm_broker import constants
from alarm_broker.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus
from alarm_broker.worker.tasks import (
    WebhookDelivery,
    _build_webhook_payload,
    _send_webhook_with_retry,
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    escalate,
    process_alarm_event,
    recover_incomplete_alarm_events,
)

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_WEBHOOK_SECRET, value_for_test
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_WEBHOOK_SECRET, value_for_test
    from helpers import FakeRedis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alarm(alarm_id: uuid.UUID | None = None, **overrides) -> Alarm:
    """Create a minimal Alarm instance for testing."""
    defaults = dict(
        id=alarm_id or uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="test",
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity="P0",
        silent=True,
        ack_token=value_for_test("worker-task-ack") + uuid.uuid4().hex[:8],
        created_at=datetime.now(UTC),
        meta={},
    )
    defaults.update(overrides)
    return Alarm(**defaults)


def _make_ctx(sessionmaker, settings, http=None) -> dict:
    """Build a minimal worker context dict."""
    return {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http or httpx.AsyncClient(),
        "redis": FakeRedis(),
        "zammad": MockZammadClient(),
        "sendxms": MockSendXmsClient(),
        "signal": MockSignalClient(),
    }


# ---------------------------------------------------------------------------
# a) test_build_webhook_payload
# ---------------------------------------------------------------------------


async def test_build_webhook_payload(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        alarm = _make_alarm(alarm_id, created_at=now)
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        payload = _build_webhook_payload(alarm, "triggered")

    expect(payload["event"] == constants.EVENT_ALARM_STATE_CHANGED)
    expect(payload["alarm_id"] == str(alarm_id))
    expect(payload["state"] == "triggered")
    expect(payload["person_id"] == "ma-012")
    expect(payload["room_id"] == "bg-1.23")
    expect(payload["site_id"] == "bg")
    expect(payload["device_id"] == "ylk-t5-10023")
    expect(payload["created_at"] is not None)
    expect(payload["acked_at"] is None)
    expect(payload["resolved_at"] is None)
    expect(payload["cancelled_at"] is None)


async def test_deleted_alarm_worker_handlers_skip_external_work(
    sessionmaker, seeded_db, settings, monkeypatch
):
    """All worker entry points stop before enrichment, delivery, or webhooks after deletion."""
    alarm_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, deleted_at=datetime.now(UTC)))
        await session.commit()

    enrich = AsyncMock()
    webhook = AsyncMock()
    monkeypatch.setattr("alarm_broker.worker.tasks.enrich_alarm_context", enrich)
    monkeypatch.setattr("alarm_broker.worker.tasks._send_webhook_with_retry", webhook)
    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/deleted"
    settings.webhook_allowed_hosts = "hooks.example.test"
    ctx = _make_ctx(sessionmaker, settings)

    await alarm_created(ctx, str(alarm_id))
    await escalate(ctx, str(alarm_id), step_no=1)
    await alarm_acked(ctx, str(alarm_id), acked_by="operator")
    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    expect(enrich.await_count == 0)
    expect(webhook.await_count == 0)


# ---------------------------------------------------------------------------
# b) test_alarm_state_changed_posts_webhook_with_hmac
# ---------------------------------------------------------------------------


async def test_alarm_state_changed_posts_webhook_with_hmac(
    sessionmaker, seeded_db, settings, monkeypatch
):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, created_at=now))
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/hmac"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "hooks.example.test"

    http = httpx.AsyncClient()
    ctx = _make_ctx(sessionmaker, settings, http)

    async def allow_webhook(_url: str) -> tuple[str, ...]:
        return ("1.1.1.1",)

    monkeypatch.setattr("alarm_broker.worker.tasks.validate_url_not_internal", allow_webhook)

    with respx.mock(assert_all_called=True) as mock_router:

        def _check_hmac(request: httpx.Request) -> httpx.Response:
            sig_header = request.headers.get("X-Hub-Signature-256", "")
            expect(sig_header.startswith("sha256="))
            sig = sig_header.removeprefix("sha256=")
            expected = hmac.new(
                TEST_WEBHOOK_SECRET.encode(), request.content, hashlib.sha256
            ).hexdigest()
            expect(sig == expected)
            return httpx.Response(200, json={"ok": True})

        mock_router.post("https://1.1.1.1/hmac").mock(side_effect=_check_hmac)
        await alarm_state_changed(ctx, str(alarm_id), "triggered")

    await http.aclose()


# ---------------------------------------------------------------------------
# c) test_process_alarm_event_dispatches_correctly
# ---------------------------------------------------------------------------


async def test_process_alarm_event_dispatches_state_changed(
    sessionmaker, seeded_db, settings, monkeypatch
):
    """process_alarm_event dispatches alarm.state_changed to alarm_state_changed."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/event"
    settings.webhook_secret = EMPTY_SECRET_VALUE
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "hooks.example.test"

    http = httpx.AsyncClient()
    ctx = _make_ctx(sessionmaker, settings, http)

    async def allow_webhook(_url: str) -> tuple[str, ...]:
        return ("1.1.1.1",)

    monkeypatch.setattr("alarm_broker.worker.tasks.validate_url_not_internal", allow_webhook)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post("https://1.1.1.1/event").respond(200, json={"ok": True})

        await process_alarm_event(
            ctx,
            {
                "event_type": constants.EVENT_ALARM_STATE_CHANGED,
                "alarm_id": str(alarm_id),
                "new_state": "triggered",
            },
        )

        expect(route.called)

    await http.aclose()


async def test_process_alarm_event_unknown_type_does_not_crash(sessionmaker, seeded_db, settings):
    """Unknown event types are logged but do not raise."""
    ctx = _make_ctx(sessionmaker, settings)

    await process_alarm_event(
        ctx,
        {
            "event_type": "alarm.unknown_event",
            "alarm_id": str(uuid.uuid4()),
        },
    )


async def test_process_alarm_event_missing_payload(sessionmaker, seeded_db, settings):
    """Invalid payloads (missing event_type) return early without raising."""
    ctx = _make_ctx(sessionmaker, settings)
    await process_alarm_event(ctx, {})


async def test_recover_incomplete_alarm_events_enqueues_missing_jobs(
    sessionmaker, seeded_db, settings
):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                meta={
                    "event_delivery": {
                        "alarm_created_enqueued": False,
                        "alarm_state_changed_enqueued": False,
                        "last_error": "queue unavailable",
                        "last_attempt_at": None,
                    }
                },
            )
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await recover_incomplete_alarm_events(ctx)

    expect(
        [args[0]["event_type"] for _name, args in ctx["redis"].jobs]
        == ["alarm.created", "alarm.state_changed"]
    )

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        delivery = alarm.meta["event_delivery"]
        expect(delivery["alarm_created_enqueued"] is True)
        expect(delivery["alarm_state_changed_enqueued"] is True)
        expect(delivery["last_error"] is None)


# ---------------------------------------------------------------------------
# d) test_escalate_skips_resolved_alarm
# ---------------------------------------------------------------------------


async def test_escalate_skips_resolved_alarm(sessionmaker, seeded_db, settings):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                status=AlarmStatus.RESOLVED,
                resolved_at=datetime.now(UTC),
            )
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise, should just skip
    await escalate(ctx, str(alarm_id), step_no=1)

    # No notification should have been logged
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
    expect(len(rows) == 0)


async def test_escalate_skips_acknowledged_alarm(sessionmaker, seeded_db, settings):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                status=AlarmStatus.ACKNOWLEDGED,
                acked_at=datetime.now(UTC),
            )
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)
    await escalate(ctx, str(alarm_id), step_no=1)

    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
    expect(len(rows) == 0)


# ---------------------------------------------------------------------------
# e) test_send_webhook_with_retry_handles_failure
# ---------------------------------------------------------------------------


async def test_send_webhook_with_retry_handles_failure(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    http = httpx.AsyncClient()
    payload_dict = {"event": "alarm.state_changed", "alarm_id": str(alarm_id), "state": "triggered"}
    payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode()

    with respx.mock as mock_router:
        mock_router.post("https://1.1.1.1/fail").respond(500, text="Internal Error")

        async with sessionmaker() as session:
            await _send_webhook_with_retry(
                http=http,
                webhook_url="https://hooks.example.test/fail",
                payload_bytes=payload_bytes,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
                delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
                resolved_addresses=("1.1.1.1",),
            )

            # Should have logged an error notification
            row = await session.scalar(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm_id)
                .where(AlarmNotification.channel == "webhook")
            )

    expect(row is not None)
    expect(row.result == "error")
    expect(row.error is not None)

    await http.aclose()


async def test_state_webhook_fails_over_to_second_validated_address(
    sessionmaker, seeded_db, monkeypatch
):
    """State callbacks keep Host/SNI while moving from a failed pin to the next pin."""
    alarm_id = uuid.uuid4()
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def post_once(_http, url, _payload, headers, _timeout, extensions):
        calls.append((url, headers, extensions))
        if "1.1.1.1" in url:
            raise RuntimeError("first address unavailable")

    monkeypatch.setattr("alarm_broker.worker.tasks._post_webhook", post_once)

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()
        await _send_webhook_with_retry(
            http=object(),
            webhook_url="https://hooks.example.test/state",
            payload_bytes=b"{}",
            headers={"Content-Type": "application/json"},
            timeout=0.01,
            delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
            resolved_addresses=("1.1.1.1", "8.8.8.8"),
        )

    expect([call[0] for call in calls] == ["https://1.1.1.1/state", "https://8.8.8.8/state"])
    expect(all(call[1]["Host"] == "hooks.example.test" for call in calls))
    expect(all(call[2]["sni_hostname"] == "hooks.example.test" for call in calls))


async def test_state_webhook_logs_one_safe_error_after_all_validated_addresses_fail(
    sessionmaker, seeded_db, monkeypatch
):
    """State callbacks do not use the hostname after each validated pin fails."""
    alarm_id = uuid.uuid4()
    secret = value_for_test("state-webhook-query")
    calls: list[str] = []

    async def fail_every_address(_http, url, _payload, _headers, _timeout, _extensions):
        calls.append(url)
        raise RuntimeError(f"delivery failed for {url}")

    monkeypatch.setattr("alarm_broker.worker.tasks._post_webhook", fail_every_address)

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()
        await _send_webhook_with_retry(
            http=object(),
            webhook_url=f"https://hooks.example.test/state?token={secret}",
            payload_bytes=b"{}",
            headers={"Content-Type": "application/json"},
            timeout=0.01,
            delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
            resolved_addresses=("1.1.1.1", "8.8.8.8"),
        )

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
        )

    expect(
        calls
        == [
            f"https://1.1.1.1/state?token={secret}",
            f"https://8.8.8.8/state?token={secret}",
        ]
    )
    expect(row is not None)
    expect(row.error is not None)
    expect(secret not in row.error)


async def test_pinned_webhook_failure_redacts_query_from_audit_and_logs(
    sessionmaker,
    seeded_db,
    caplog,
):
    alarm_id = uuid.uuid4()
    secret = value_for_test("webhook-query")

    class FailingHttp:
        async def post(self, url, **_kwargs):  # noqa: ANN001
            raise RuntimeError(f"request failed for {url}")

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

        caplog.set_level(logging.ERROR, logger="alarm_broker")
        await _send_webhook_with_retry(
            http=FailingHttp(),
            webhook_url=f"https://hooks.example.test/fail?token={secret}",
            payload_bytes=b"{}",
            headers={"Content-Type": "application/json"},
            timeout=0.01,
            delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
            resolved_addresses=("203.0.113.10",),
        )

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
        )

    assert row is not None
    assert row.error is not None
    assert secret not in row.error
    assert secret not in caplog.text
