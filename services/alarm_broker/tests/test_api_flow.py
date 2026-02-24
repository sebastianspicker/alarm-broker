from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

pytestmark = [pytest.mark.integration]


@pytest.mark.asyncio
async def test_yealink_idempotent_and_ack(
    engine, sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    monkeypatch.setattr("alarm_broker.api.routes.yealink.bucket_10s", lambda: 123)
    monkeypatch.setattr("alarm_broker.api.routes.yealink.minute_bucket", lambda: 456)

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert r1.status_code == 200, r1.text
            alarm_id_1 = uuid.UUID(r1.json()["alarm_id"])

            r2 = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert r2.status_code == 200, r2.text
            alarm_id_2 = uuid.UUID(r2.json()["alarm_id"])

            assert alarm_id_1 == alarm_id_2
            assert [name for name, _args in fake_redis.jobs] == ["alarm_created"]

    async with sessionmaker() as session:
        alarms = (await session.scalars(select(Alarm))).all()
        assert len(alarms) == 1
        alarm = alarms[0]
        assert alarm.status == AlarmStatus.TRIGGERED
        assert alarm.ack_token

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r3 = await client.get(f"/a/{alarm.ack_token}")
            assert r3.status_code == 200
            assert "Alarm übernehmen" in r3.text

            r4 = await client.post(
                f"/a/{alarm.ack_token}", data={"acked_by": "Tester", "note": "On my way"}
            )
            assert r4.status_code == 200

    async with sessionmaker() as session:
        alarm2 = await session.get(Alarm, alarm_id_1)
        assert alarm2
        assert alarm2.status == AlarmStatus.ACKNOWLEDGED

    assert [name for name, _args in fake_redis.jobs] == ["alarm_created", "alarm_acked"]


@pytest.mark.asyncio
async def test_rate_limit_applies_only_to_new_alarms(
    engine, seeded_db, fake_redis, settings, monkeypatch
):
    # Allow 1 new alarm per minute for the test (settings injected into app)
    settings.rate_limit_per_minute = 1
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    buckets = iter([1, 2])
    monkeypatch.setattr("alarm_broker.api.routes.yealink.bucket_10s", lambda: next(buckets))
    monkeypatch.setattr("alarm_broker.api.routes.yealink.minute_bucket", lambda: 999)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert r1.status_code == 200

            r2 = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert r2.status_code == 429
