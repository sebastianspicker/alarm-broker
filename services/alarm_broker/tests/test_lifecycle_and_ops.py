from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

pytestmark = [pytest.mark.integration]


async def _trigger_alarm(client: AsyncClient) -> uuid.UUID:
    response = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["alarm_id"])


@pytest.mark.asyncio
async def test_readyz_healthy(engine, seeded_db, fake_redis, settings):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ok"] == "true"


@pytest.mark.asyncio
async def test_readyz_redis_unhealthy_returns_503(engine, seeded_db, settings):
    class BrokenRedis:
        async def get(self, _key: str):
            raise RuntimeError("redis unavailable")

    app = create_app(settings=settings, injected_engine=engine, injected_redis=BrokenRedis())

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["redis"] == "down"


@pytest.mark.asyncio
async def test_readyz_db_unhealthy_returns_503(engine, seeded_db, fake_redis, settings):
    class BrokenSessionmaker:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        app.state.sessionmaker = BrokenSessionmaker()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["db"] == "down"


@pytest.mark.asyncio
async def test_alarm_resolve_success_and_invalid_transition(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            resolve_response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops", "note": "handled"},
            )
            assert resolve_response.status_code == 204

            invalid_response = await client.post(
                f"/v1/alarms/{alarm_id}/cancel",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops", "note": "too late"},
            )
            assert invalid_response.status_code == 409

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.status == AlarmStatus.RESOLVED
        assert alarm.resolved_by == "Ops"
        assert alarm.resolved_at is not None


@pytest.mark.asyncio
async def test_alarm_resolve_idempotent(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            first = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops"},
            )
            second = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops"},
            )

    assert first.status_code == 204
    assert second.status_code == 204


@pytest.mark.asyncio
async def test_alarm_transition_rejects_oversized_actor(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)
            response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "A" * 121},
            )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_alarm_pagination_cursor(engine, sessionmaker, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"

    now = datetime.now(UTC)
    alarm_ids: list[uuid.UUID] = []

    async with sessionmaker() as session:
        for index in range(3):
            alarm_id = uuid.uuid4()
            alarm_ids.append(alarm_id)
            session.add(
                Alarm(
                    id=alarm_id,
                    status=AlarmStatus.TRIGGERED,
                    source="test",
                    event="alarm.trigger",
                    person_id="ma-012",
                    room_id="bg-1.23",
                    site_id="bg",
                    device_id="ylk-t5-10023",
                    severity="P0",
                    silent=True,
                    ack_token=f"token-{index}",
                    created_at=now - timedelta(minutes=index),
                    meta={},
                )
            )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            page_1 = await client.get(
                "/v1/alarms",
                params={"limit": 2},
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert page_1.status_code == 200
            assert len(page_1.json()) == 2
            assert "X-Next-Cursor" in page_1.headers

            cursor = page_1.headers["X-Next-Cursor"]
            page_2 = await client.get(
                "/v1/alarms",
                params={"limit": 2, "cursor": cursor},
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert page_2.status_code == 200
            assert len(page_2.json()) >= 1


@pytest.mark.unit
def test_docs_index_links_exist() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    docs_index = repo_root / "docs" / "README.md"
    text = docs_index.read_text(encoding="utf-8")

    for line in text.splitlines():
        if "`" not in line:
            continue
        parts = line.split("`")
        if len(parts) < 3:
            continue
        candidate = parts[1]
        if not candidate.endswith(".md"):
            continue
        assert (repo_root / "docs" / candidate).exists(), f"Missing doc: {candidate}"
