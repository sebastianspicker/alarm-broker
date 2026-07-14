from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.constants import TEST_ADMIN_API_KEY, value_for_test
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY, value_for_test


pytestmark = pytest.mark.integration


def _alarm(*, alarm_id: int, severity: str, source: str) -> Alarm:
    return Alarm(
        id=uuid.UUID(int=alarm_id),
        status=AlarmStatus.TRIGGERED,
        source=source,
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity=severity,
        silent=True,
        ack_token=value_for_test(f"admin-page-{alarm_id}"),
        created_at=datetime.now(UTC),
        meta={},
    )


def _alarm_ids(page: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'href="/admin/alarms/([0-9a-f-]+)\?lang=', page)))


def _next_page_url(page: str) -> str:
    match = re.search(r'<a class="button" href="([^"]+)">[^<]+</a></p>', page)
    assert match is not None
    return html.unescape(match.group(1))


async def test_admin_cursor_uses_selected_sort_and_preserves_query(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    source = "pagination&special"
    alarms = [
        _alarm(alarm_id=2, severity="P0", source=source),
        _alarm(alarm_id=4, severity="P1", source=source),
        _alarm(alarm_id=1, severity="P2", source=source),
    ]
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/admin/login",
                data={"admin_key": TEST_ADMIN_API_KEY, "operator_name": "Pagination Ops"},
            )
            assert login.status_code == 303
            first = await client.get(
                "/admin",
                params={
                    "search": source,
                    "sort_by": "severity",
                    "order": "asc",
                    "limit": 2,
                    "lang": "en",
                },
            )
            next_url = _next_page_url(first.text)
            second = await client.get(next_url)

    assert _alarm_ids(first.text) == [str(alarms[0].id), str(alarms[1].id)]
    assert _alarm_ids(second.text) == [str(alarms[2].id)]
    params = parse_qs(urlsplit(next_url).query)
    assert params == {
        "search": [source],
        "sort_by": ["severity"],
        "order": ["asc"],
        "limit": ["2"],
        "lang": ["en"],
        "cursor": [str(alarms[1].id)],
    }
