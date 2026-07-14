"""Regression coverage for trigger idempotency and credential-safe device IDs."""

from __future__ import annotations

import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, Device
from alarm_broker.services.alarm_service import acknowledge_alarm
from alarm_broker.services.trigger_service import TriggerService

try:
    from tests.assertions import expect
    from tests.constants import TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN, value_for_test
except ModuleNotFoundError:
    from assertions import expect
    from constants import TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN, value_for_test

pytestmark = [pytest.mark.integration]


async def test_idempotency_lease_survives_adjacent_time_buckets(
    sessionmaker, seeded_db, fake_redis, settings
):
    """A 30-second reservation must not change ownership at a 10-second boundary."""
    async with sessionmaker() as creator_session:
        creator = TriggerService(
            creator_session,
            fake_redis,
            settings,
            idempotency_bucket=100,
            rate_limit_bucket=200,
        )
        first = await creator.process_trigger(
            token=TEST_DEVICE_TOKEN,
            client_ip="127.0.0.1",
            user_agent="first",
        )

    async with sessionmaker() as duplicate_session:
        duplicate = TriggerService(
            duplicate_session,
            fake_redis,
            settings,
            idempotency_bucket=101,
            rate_limit_bucket=200,
        )
        second = await duplicate.process_trigger(
            token=TEST_DEVICE_TOKEN,
            client_ip="127.0.0.1",
            user_agent="adjacent-boundary",
        )

        alarms = (await duplicate_session.scalars(select(Alarm))).all()

    expect(first.success)
    expect(second.success)
    expect(second.is_duplicate is True)
    expect(second.alarm_id == first.alarm_id)
    expect(len(alarms) == 1)


async def test_admin_generated_device_id_never_exposes_bearer_token(
    engine, sessionmaker, seeded_db, fake_redis, settings, caplog
):
    """Generated device IDs must stay opaque in alarms, logs, and queued events."""
    token = value_for_test("admin-created-device-token")
    caplog.set_level(logging.INFO, logger="alarm_broker")
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_response = await client.post(
                "/v1/admin/devices",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={
                    "device_token": token,
                    "person_id": "ma-012",
                    "room_id": "bg-1.23",
                },
            )
            trigger_response = await client.get("/v1/yealink/alarm", params={"token": token})

    expect(create_response.status_code == 201, create_response.text)
    expect(trigger_response.status_code == 200, trigger_response.text)
    device_id = create_response.json()["device_id"]
    expect(device_id.startswith("device:"))
    expect(token not in device_id)
    uuid.UUID(device_id.removeprefix("device:"))

    async with sessionmaker() as session:
        device = await session.scalar(select(Device).where(Device.device_token == token))
        alarm = await session.scalar(
            select(Alarm).where(Alarm.id == uuid.UUID(trigger_response.json()["alarm_id"]))
        )

    expect(device is not None)
    expect(device.id == device_id)
    expect(alarm is not None)
    expect(alarm.device_id == device_id)
    expect(token not in str(alarm.meta))
    expect(all(token not in str(args) for _name, args in fake_redis.jobs))

    trigger_records = [record for record in caplog.records if record.message == "alarm_triggered"]
    expect(len(trigger_records) == 1)
    expect(trigger_records[0].device_id == device_id)
    expect(token not in trigger_records[0].getMessage())


async def test_event_delivery_merge_preserves_a_concurrent_lifecycle_note(
    sessionmaker, seeded_db, fake_redis, settings
) -> None:
    """Recovery metadata must not replace top-level fields written after its initial read."""
    async with sessionmaker() as session:
        trigger = TriggerService(session, fake_redis, settings, rate_limit_bucket=300)
        result = await trigger.process_trigger(
            token=TEST_DEVICE_TOKEN,
            client_ip="127.0.0.1",
            user_agent="metadata-race",
        )
    assert result.success
    assert result.alarm_id is not None

    async with sessionmaker() as recovery_session:
        stale_alarm = await recovery_session.get(Alarm, result.alarm_id)
        async with sessionmaker() as lifecycle_session:
            current_alarm = await lifecycle_session.get(Alarm, result.alarm_id)
            assert await acknowledge_alarm(
                lifecycle_session,
                current_alarm,
                acked_by="metadata-safe",
                note="keep this note",
            )

        recovery = TriggerService(recovery_session, fake_redis, settings)
        await recovery._persist_event_delivery_state(
            stale_alarm,
            alarm_created_enqueued=True,
            last_error="transient queue state",
        )

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, result.alarm_id)

    assert persisted.meta["ack_note"] == "keep this note"
    assert persisted.meta["event_delivery"]["alarm_created_enqueued"] is True
