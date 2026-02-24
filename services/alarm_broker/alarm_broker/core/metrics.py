from __future__ import annotations

from collections import Counter
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus

_lock = Lock()
_http_requests_total: Counter[tuple[str, str, str]] = Counter()
_http_request_duration_ms_total: Counter[tuple[str, str, str]] = Counter()
_events_total: Counter[str] = Counter()


def record_http_request(*, method: str, route: str, status_code: int, duration_ms: int) -> None:
    key = (method.upper(), route, str(status_code))
    with _lock:
        _http_requests_total[key] += 1
        _http_request_duration_ms_total[key] += max(0, int(duration_ms))


def record_event(event: str) -> None:
    with _lock:
        _events_total[event] += 1


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _http_labels(method: str, route: str, status_code: str) -> str:
    return (
        f'method="{_escape(method)}",route="{_escape(route)}",status_code="{_escape(status_code)}"'
    )


async def _alarm_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(select(Alarm.status, func.count(Alarm.id)).group_by(Alarm.status))
    ).all()
    counts = {status.value: 0 for status in AlarmStatus}
    for status, count in rows:
        counts[status.value] = int(count)
    return counts


async def _notification_counts(session: AsyncSession) -> list[tuple[str, str, int]]:
    rows = (
        await session.execute(
            select(
                AlarmNotification.channel,
                func.coalesce(AlarmNotification.result, "unknown"),
                func.count(AlarmNotification.id),
            ).group_by(AlarmNotification.channel, AlarmNotification.result)
        )
    ).all()
    return [(str(channel), str(result), int(count)) for channel, result, count in rows]


async def render_prometheus_metrics(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> str:
    lines: list[str] = []

    lines.append("# HELP alarm_broker_http_requests_total Total number of HTTP requests.")
    lines.append("# TYPE alarm_broker_http_requests_total counter")
    with _lock:
        http_requests_snapshot = dict(_http_requests_total)
        http_duration_snapshot = dict(_http_request_duration_ms_total)
        events_snapshot = dict(_events_total)

    for (method, route, status_code), value in sorted(http_requests_snapshot.items()):
        labels = _http_labels(method, route, status_code)
        lines.append(f"alarm_broker_http_requests_total{{{labels}}} {value}")

    lines.append(
        "# HELP alarm_broker_http_request_duration_ms_total Total request duration in milliseconds."
    )
    lines.append("# TYPE alarm_broker_http_request_duration_ms_total counter")
    for (method, route, status_code), value in sorted(http_duration_snapshot.items()):
        labels = _http_labels(method, route, status_code)
        lines.append(f"alarm_broker_http_request_duration_ms_total{{{labels}}} {value}")

    lines.append("# HELP alarm_broker_events_total Total number of internal events.")
    lines.append("# TYPE alarm_broker_events_total counter")
    for event, value in sorted(events_snapshot.items()):
        lines.append(f'alarm_broker_events_total{{event="{_escape(event)}"}} {value}')

    async with sessionmaker() as session:
        by_status = await _alarm_counts(session)
        by_notification = await _notification_counts(session)

    lines.append("# HELP alarm_broker_alarms_by_status Number of alarms by status.")
    lines.append("# TYPE alarm_broker_alarms_by_status gauge")
    for state, count in sorted(by_status.items()):
        lines.append(f'alarm_broker_alarms_by_status{{status="{_escape(state)}"}} {count}')

    lines.append(
        "# HELP alarm_broker_notifications_total Notification attempts grouped by channel/result."
    )
    lines.append("# TYPE alarm_broker_notifications_total counter")
    for channel, result, count in by_notification:
        lines.append(
            "alarm_broker_notifications_total"
            f'{{channel="{_escape(channel)}",result="{_escape(result)}"}} {count}'
        )

    lines.append("")
    return "\n".join(lines)
