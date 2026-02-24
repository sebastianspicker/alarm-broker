from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_redis, get_session
from alarm_broker.api.schemas import AckIn
from alarm_broker.services.ack_ui import render_ack_page
from alarm_broker.services.alarm_service import acknowledge_alarm, get_alarm_by_ack_token
from alarm_broker.services.enrichment_service import enrich_alarm_context
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)

router = APIRouter()
logger = logging.getLogger("alarm_broker")


@router.get("/a/{ack_token}", response_class=HTMLResponse)
async def ack_page(
    ack_token: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    alarm = await get_alarm_by_ack_token(session, ack_token)
    if not alarm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown token")

    enriched = await enrich_alarm_context(session, alarm)
    return HTMLResponse(render_ack_page(alarm, enriched))


@router.post("/a/{ack_token}", response_class=HTMLResponse)
async def ack_submit(
    request: Request,
    ack_token: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    alarm = await get_alarm_by_ack_token(session, ack_token)
    if not alarm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown token")

    form = await request.form()
    acked_by = (form.get("acked_by") or "").strip() or None
    note = (form.get("note") or "").strip() or None
    try:
        payload = AckIn(acked_by=acked_by, note=note)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    changed = await acknowledge_alarm(
        session,
        alarm,
        acked_by=payload.acked_by,
        note=payload.note,
    )
    if changed:
        request.state.alarm_id = str(alarm.id)
        redis = get_redis(request)
        await enqueue_alarm_acked_event(
            redis,
            alarm_id=alarm.id,
            acked_by=payload.acked_by,
            note=payload.note,
            logger=logger,
        )
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )

    enriched = await enrich_alarm_context(session, alarm)
    return HTMLResponse(render_ack_page(alarm, enriched))
