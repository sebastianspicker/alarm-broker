from __future__ import annotations

import secrets
from datetime import UTC, datetime
from html import escape
from string import Template

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_session
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.settings import Settings

router = APIRouter()

_PAGE = Template(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="${refresh_seconds}">
  <title>Alarm Operations Console</title>
  <style>
    :root {
      --ink: #0f172a;
      --paper: #f8f5ee;
      --panel: #fffefb;
      --accent: #005f73;
      --alert: #9b2226;
      --ok: #2a9d8f;
      --muted: #415a77;
      --line: #d6d3c9;
      --radius: 14px;
      --shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 15% 10%, rgba(0,95,115,0.12), transparent 42%),
        radial-gradient(circle at 85% 80%, rgba(155,34,38,0.10), transparent 45%),
        var(--paper);
      font-family: "Avenir Next", "Trebuchet MS", sans-serif;
      min-height: 100vh;
      padding: 18px;
    }
    .shell {
      max-width: 1120px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }
    .hero {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: 20px;
    }
    .title {
      margin: 0 0 8px;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      letter-spacing: 0.02em;
      font-size: clamp(1.4rem, 3vw, 2.1rem);
    }
    .meta {
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      padding: 12px;
    }
    .card h3 {
      margin: 0;
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .card p {
      margin: 8px 0 0;
      font-size: 1.5rem;
      font-weight: 700;
    }
    .table-wrap {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 920px;
      font-family: "SF Mono", Menlo, Monaco, monospace;
      font-size: 0.84rem;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #f2efe7;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 700;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .state {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 700;
      font-size: 0.76rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: #eef2ff;
      color: #1e3a8a;
    }
    .state.triggered { background: #fef2f2; color: var(--alert); }
    .state.acknowledged { background: #ecfeff; color: var(--accent); }
    .state.resolved { background: #ecfdf3; color: #166534; }
    .state.cancelled { background: #f3f4f6; color: #374151; }
    .muted { color: var(--muted); }
    @media (max-width: 880px) {
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      body { padding: 10px; }
      .hero { padding: 14px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1 class="title">Alarm Operations Console</h1>
      <p class="meta">
        <span>Auto-refresh: ${refresh_seconds}s</span>
        <span>Rows: ${row_count}</span>
        <span>Updated: ${generated_at}</span>
      </p>
    </section>
    <section class="cards">
      ${status_cards}
    </section>
    <section class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Alarm ID</th>
            <th>Created</th>
            <th>Person</th>
            <th>Room</th>
            <th>Source</th>
            <th>Severity</th>
            <th>Ack</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>"""
)


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
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    _ensure_admin_key(settings, key)

    alarms = (
        await session.scalars(
            select(Alarm).order_by(Alarm.created_at.desc(), Alarm.id.desc()).limit(limit)
        )
    ).all()
    counts_rows = (
        await session.execute(select(Alarm.status, func.count(Alarm.id)).group_by(Alarm.status))
    ).all()
    counts = {status.value: 0 for status in AlarmStatus}
    for status_value, count in counts_rows:
        counts[status_value.value] = int(count)

    status_cards = []
    for state in ("triggered", "acknowledged", "resolved", "cancelled"):
        status_cards.append(
            f"<article class='card'><h3>{escape(state)}</h3><p>{counts.get(state, 0)}</p></article>"
        )

    rows = []
    for alarm in alarms:
        alarm_state = escape(alarm.status.value)
        rows.append(
            "<tr>"
            f"<td><span class='state {alarm_state}'>{alarm_state}</span></td>"
            f"<td>{escape(str(alarm.id))}</td>"
            f"<td class='muted'>{escape(alarm.created_at.isoformat())}</td>"
            f"<td>{escape(str(alarm.person_id or '-'))}</td>"
            f"<td>{escape(str(alarm.room_id or '-'))}</td>"
            f"<td>{escape(alarm.source)}</td>"
            f"<td>{escape(alarm.severity)}</td>"
            f"<td>{escape(str(alarm.acked_by or '-'))}</td>"
            "</tr>"
        )

    page = _PAGE.substitute(
        refresh_seconds=refresh,
        row_count=str(len(alarms)),
        generated_at=escape(datetime.now(UTC).isoformat()),
        status_cards="\n".join(status_cards),
        rows="\n".join(rows)
        if rows
        else "<tr><td colspan='8' class='muted'>No alarms found</td></tr>",
    )
    return HTMLResponse(content=page)
