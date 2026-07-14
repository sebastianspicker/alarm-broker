from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select

from alarm_broker.db.models import Alarm, AlarmEventOutbox, AlarmStatus
from alarm_broker.services.alarm_service import acknowledge_alarm
from alarm_broker.services.event_service import (
    dispatch_pending_alarm_events,
    has_pending_alarm_events,
)

try:
    from tests.constants import value_for_test
except ModuleNotFoundError:
    from constants import value_for_test


pytestmark = pytest.mark.integration
logger = logging.getLogger("alarm_broker.tests")


class _UnavailableRedis:
    async def enqueue_job(self, *_args, **_kwargs):
        raise ConnectionError("queue unavailable")


class _FailFirstRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def enqueue_job(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("first event unavailable")
        return object()


def _alarm() -> Alarm:
    return Alarm(
        id=uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="outbox-test",
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity="P0",
        silent=True,
        ack_token=value_for_test("outbox-ack"),
        meta={},
    )


async def test_lifecycle_events_survive_queue_outage_and_recover(
    sessionmaker, seeded_db, fake_redis
) -> None:
    alarm = _alarm()
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()
        persisted = await session.get(Alarm, alarm.id)
        assert await acknowledge_alarm(
            session, persisted, acked_by="Recovery Ops", note="Recovered later"
        )

        published = await dispatch_pending_alarm_events(
            session, _UnavailableRedis(), logger=logger, alarm_id=alarm.id
        )
        assert published == 0
        assert await has_pending_alarm_events(session, alarm.id)

    async with sessionmaker() as session:
        pending = list(
            (
                await session.scalars(
                    select(AlarmEventOutbox).where(AlarmEventOutbox.alarm_id == alarm.id)
                )
            ).all()
        )
        assert len(pending) == 2
        assert all(event.published_at is None for event in pending)
        ordered = sorted(pending, key=lambda event: event.sequence)
        assert [event.attempts for event in ordered] == [1, 0]
        assert ordered[0].last_error == "queue unavailable"
        assert ordered[1].last_error is None

        published = await dispatch_pending_alarm_events(
            session, fake_redis, logger=logger, alarm_id=alarm.id
        )
        assert published == 2
        assert not await has_pending_alarm_events(session, alarm.id)
        assert (
            await dispatch_pending_alarm_events(
                session, fake_redis, logger=logger, alarm_id=alarm.id
            )
            == 0
        )

    assert [args[0]["event_type"] for _name, args in fake_redis.jobs] == [
        "alarm.acknowledged",
        "alarm.state_changed",
    ]


async def test_outbox_stops_an_alarm_stream_after_the_first_failed_event(
    sessionmaker, seeded_db, fake_redis
) -> None:
    alarm = _alarm()
    fail_first = _FailFirstRedis()
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()
        persisted = await session.get(Alarm, alarm.id)
        assert await acknowledge_alarm(session, persisted, acked_by="Ordered Ops")

        published = await dispatch_pending_alarm_events(
            session, fail_first, logger=logger, alarm_id=alarm.id
        )
        assert published == 0
        assert fail_first.calls == 1

        pending = list(
            (
                await session.scalars(
                    select(AlarmEventOutbox)
                    .where(AlarmEventOutbox.alarm_id == alarm.id)
                    .order_by(AlarmEventOutbox.sequence)
                )
            ).all()
        )
        assert [event.attempts for event in pending] == [1, 0]

        assert (
            await dispatch_pending_alarm_events(
                session, fake_redis, logger=logger, alarm_id=alarm.id
            )
            == 2
        )

    assert [args[0]["event_type"] for _name, args in fake_redis.jobs] == [
        "alarm.acknowledged",
        "alarm.state_changed",
    ]
