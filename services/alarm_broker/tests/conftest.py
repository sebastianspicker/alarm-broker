from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from alarm_broker.api.main import create_app
from alarm_broker.db.base import Base
from alarm_broker.db.models import Device, Person, Room, Site
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import Settings


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self.jobs: list[tuple[str, tuple]] = []

    async def close(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True

    async def enqueue_job(self, name: str, *args, **kwargs) -> None:  # noqa: ARG002
        self.jobs.append((name, args))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def engine(tmp_path) -> AsyncIterator[AsyncEngine]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return create_sessionmaker(engine)


@pytest_asyncio.fixture
async def seeded_db(sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        session.add(Site(id="bg", name="Standort BG"))
        session.add(Room(id="bg-1.23", site_id="bg", label="Raum 1.23", floor="1"))
        session.add(Person(id="ma-012", display_name="Person X", role="Mitarbeiterin", active=True))
        session.add(
            Device(
                id="ylk-t5-10023",
                vendor="yealink",
                model_family="T5",
                account_ext="10023",
                device_token="YLK_T54W_3F9A",
                person_id="ma-012",
                room_id="bg-1.23",
            )
        )
        await session.commit()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key="dev-admin-key",
        rate_limit_per_minute=10,
        zammad_api_token="",
        sendxms_enabled=False,
        signal_enabled=False,
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(settings: Settings, engine: AsyncEngine, fake_redis: FakeRedis):
    return create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
