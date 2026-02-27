from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from string import Template

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_session
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.settings import Settings

router = APIRouter()

# Load external template
_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "admin.html"


def _load_template() -> Template:
    """Load admin UI template with fallback for testing environments."""
    try:
        return Template(_TEMPLATE_PATH.read_text())
    except FileNotFoundError:
        # Fallback for testing - return a minimal template
        return Template("""
<!DOCTYPE html>
<html>
<head><title>Admin</title></head>
<body><h1>Alarm Admin</h1></body>
</html>
""")


_TEMPLATE = _load_template()


def _ensure_admin_key(settings: Settings, key: str | None) -> None:
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Admin key not configured"
        )
    if not secrets.compare_digest(key or "", settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    key: str | None = Query(default=None),
    refresh: int = Query(default=10, ge=5, le=120),
    limit: int = Query(default=100, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    _ensure_admin_key(settings, key)

    # Build query with optional status filter
    stmt = select(Alarm).order_by(Alarm.created_at.desc(), Alarm.id.desc())
    if status_filter and status_filter in [s.value for s in AlarmStatus]:
        stmt = stmt.where(Alarm.status == AlarmStatus(status_filter))
    stmt = stmt.limit(limit)
    alarms = (await session.scalars(stmt)).all()

    # Get counts (total and by status)
    total_count = await session.scalar(select(func.count(Alarm.id)))
    counts_rows = (
        await session.execute(select(Alarm.status, func.count(Alarm.id)).group_by(Alarm.status))
    ).all()
    counts = {status.value: 0 for status in AlarmStatus}
    for status_value, count in counts_rows:
        counts[status_value.value] = int(count)

    status_cards = []
    for state in ("triggered", "acknowledged", "resolved", "cancelled"):
        active_class = "active" if state == status_filter else ""
        count = counts.get(state, 0)
        status_cards.append(
            f"<article class='card {active_class}'><h3>{escape(state)}</h3><p>{count}</p></article>"
        )

    rows = []
    for alarm in alarms:
        alarm_state = escape(alarm.status.value)
        alarm_id = str(alarm.id)
        alarm_short_id = escape(alarm_id[:8])
        # Calculate time since creation
        created_at = alarm.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        time_diff = datetime.now(UTC) - created_at
        minutes_ago = int(time_diff.total_seconds() / 60)
        if minutes_ago < 60:
            time_display = f"{minutes_ago}m ago"
        else:
            time_display = f"{minutes_ago // 60}h {minutes_ago % 60}m ago"
        created_iso = created_at.isoformat()
        person_display = str(alarm.person_id or "-")
        room_display = str(alarm.room_id or "-")
        source_display = alarm.source
        severity_display = alarm.severity
        acked_by_display = str(alarm.acked_by or "-")

        can_ack = alarm.status == AlarmStatus.TRIGGERED
        can_resolve = alarm.status in (AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED)
        ack_disabled_attr = " disabled" if not can_ack else ""
        resolve_disabled_attr = " disabled" if not can_resolve else ""
        search_blob = " ".join(
            [
                alarm_id,
                alarm_state,
                person_display,
                room_display,
                source_display,
                severity_display,
                acked_by_display,
            ]
        ).lower()

        rows.append(
            "<tr class='alarm-row'"
            f" data-alarm-id='{escape(alarm_id)}'"
            f" data-short-id='{alarm_short_id}'"
            f" data-status='{alarm_state}'"
            f" data-created='{escape(time_display)} ({escape(created_iso)})'"
            f" data-person='{escape(person_display)}'"
            f" data-room='{escape(room_display)}'"
            f" data-source='{escape(source_display)}'"
            f" data-severity='{escape(severity_display)}'"
            f" data-acked-by='{escape(acked_by_display)}'"
            f" data-can-ack='{'true' if can_ack else 'false'}'"
            f" data-can-resolve='{'true' if can_resolve else 'false'}'"
            f" data-search='{escape(search_blob)}'>"
            f"<td><span class='state {alarm_state}'>{alarm_state}</span></td>"
            f"<td class='mono'>{alarm_short_id}...</td>"
            f"<td class='muted'>{escape(time_display)}</td>"
            f"<td>{escape(person_display)}</td>"
            f"<td>{escape(room_display)}</td>"
            f"<td>{escape(source_display)}</td>"
            f"<td><span class='severity'>{escape(severity_display)}</span></td>"
            f"<td>{escape(acked_by_display)}</td>"
            "<td class='actions'>"
            "<button type='button' class='btn detail-btn'>Details</button>"
            "<button type='button' class='btn btn-ack quick-ack-btn'"
            f"{ack_disabled_attr}>Quick Ack</button>"
            "<button type='button' class='btn btn-resolve quick-resolve-btn'"
            f"{resolve_disabled_attr}>Quick Resolve</button>"
            "</td>"
            "</tr>"
        )

    # Build filter query string for links
    filter_qs = f"status={status_filter}&" if status_filter else ""
    if settings.simulation_enabled:
        simulation_panel = (
            "<section id='simulation-panel' class='sim-panel' data-enabled='true'>"
            "<div class='sim-head'>"
            "<h2>Simulation Mode</h2>"
            "<p class='muted'>Monitor mock notifications and demo seed helpers.</p>"
            "</div>"
            "<p id='sim-status' class='muted'>Checking simulation status ...</p>"
            "<p class='muted'>Notifications: <strong id='sim-count'>-</strong></p>"
            "<div class='sim-actions'>"
            "<button id='sim-refresh-btn' type='button' class='btn'>Refresh</button>"
            "<button id='sim-clear-btn' type='button' class='btn'>Clear Notifications</button>"
            "<button id='sim-seed-btn' type='button' class='btn'>Load Seed Info</button>"
            "</div>"
            "</section>"
        )
    else:
        simulation_panel = (
            "<section id='simulation-panel' class='sim-panel' data-enabled='false'>"
            "<div class='sim-head'>"
            "<h2>Simulation Mode</h2>"
            "<p class='muted'>Simulation mode is currently disabled on this server.</p>"
            "</div>"
            "</section>"
        )

    page = _TEMPLATE.substitute(
        refresh_seconds=refresh,
        row_count=str(len(alarms)),
        total_count=str(total_count or 0),
        generated_at=escape(datetime.now(UTC).isoformat()),
        status_cards="\n".join(status_cards),
        filter_qs=filter_qs,
        simulation_panel=simulation_panel,
        admin_key_json=json.dumps(key or ""),
        rows="\n".join(rows)
        if rows
        else "<tr><td colspan='9' class='muted'>No alarms found</td></tr>",
    )
    return HTMLResponse(content=page)
