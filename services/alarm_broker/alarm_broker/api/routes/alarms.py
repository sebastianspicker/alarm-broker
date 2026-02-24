from __future__ import annotations

import logging
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_redis, get_session, require_admin
from alarm_broker.api.schemas import (
    AckIn,
    AlarmNoteIn,
    AlarmNoteOut,
    AlarmOut,
    BulkAckIn,
    BulkOperationOut,
    BulkTransitionIn,
    TransitionIn,
)
from alarm_broker.db.models import Alarm, AlarmNote, AlarmStatus
from alarm_broker.services.alarm_service import (
    acknowledge_alarm,
    get_alarm_or_404,
    transition_alarm,
)
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)

router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])
logger = logging.getLogger("alarm_broker")


class SortOrder(StrEnum):
    """Sort order for alarm listing."""

    DESC = "desc"
    ASC = "asc"


class SortField(StrEnum):
    """Fields available for sorting alarms."""

    CREATED_AT = "created_at"
    STATUS = "status"
    SEVERITY = "severity"


@router.get("", response_model=list[AlarmOut])
async def list_alarms(
    response: Response,
    # Filtering
    status: AlarmStatus | None = None,
    severity: str | None = None,
    person_id: str | None = None,
    room_id: str | None = None,
    site_id: str | None = None,
    device_id: str | None = None,
    source: str | None = None,
    # Date range filtering
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    # Pagination
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    # Sorting
    sort_by: SortField = SortField.CREATED_AT,
    sort_order: SortOrder = SortOrder.DESC,
    session: AsyncSession = Depends(get_session),
) -> list[AlarmOut]:
    """List alarms with filtering, pagination, and sorting.

    Args:
        response: FastAPI response for setting headers
        status: Filter by alarm status
        severity: Filter by severity level
        person_id: Filter by person ID
        room_id: Filter by room ID
        site_id: Filter by site ID
        device_id: Filter by device ID
        source: Filter by trigger source
        created_after: Filter alarms created after this datetime
        created_before: Filter alarms created before this datetime
        limit: Maximum number of results (1-200)
        cursor: Pagination cursor (alarm ID)
        sort_by: Field to sort by
        sort_order: Sort order (asc or desc)
        session: Database session

    Returns:
        List of alarms matching the criteria
    """
    stmt = select(Alarm)

    # Apply filters
    if status is not None:
        stmt = stmt.where(Alarm.status == status)

    if severity is not None:
        stmt = stmt.where(Alarm.severity == severity)

    if person_id is not None:
        stmt = stmt.where(Alarm.person_id == person_id)

    if room_id is not None:
        stmt = stmt.where(Alarm.room_id == room_id)

    if site_id is not None:
        stmt = stmt.where(Alarm.site_id == site_id)

    if device_id is not None:
        stmt = stmt.where(Alarm.device_id == device_id)

    if source is not None:
        stmt = stmt.where(Alarm.source == source)

    if created_after is not None:
        stmt = stmt.where(Alarm.created_at >= created_after)

    if created_before is not None:
        stmt = stmt.where(Alarm.created_at <= created_before)

    # Apply cursor pagination
    if cursor is not None:
        cursor_alarm = await session.get(Alarm, cursor)
        if cursor_alarm:
            if sort_order == SortOrder.DESC:
                stmt = stmt.where(
                    or_(
                        Alarm.created_at < cursor_alarm.created_at,
                        and_(
                            Alarm.created_at == cursor_alarm.created_at,
                            Alarm.id < cursor_alarm.id,
                        ),
                    )
                )
            else:
                stmt = stmt.where(
                    or_(
                        Alarm.created_at > cursor_alarm.created_at,
                        and_(
                            Alarm.created_at == cursor_alarm.created_at,
                            Alarm.id > cursor_alarm.id,
                        ),
                    )
                )

    # Apply sorting
    sort_column = getattr(Alarm, sort_by.value)
    if sort_order == SortOrder.DESC:
        stmt = stmt.order_by(sort_column.desc(), Alarm.id.desc())
    else:
        stmt = stmt.order_by(sort_column.asc(), Alarm.id.asc())

    # Apply limit
    stmt = stmt.limit(limit + 1)
    alarms = list((await session.scalars(stmt)).all())

    has_more = len(alarms) > limit
    page = alarms[:limit]
    if has_more and page:
        response.headers["X-Next-Cursor"] = str(page[-1].id)

    return [AlarmOut.model_validate(alarm, from_attributes=True) for alarm in page]


@router.get("/stats")
async def alarm_stats(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get alarm statistics.

    Returns counts by status, severity, and time-based aggregations.
    """
    from sqlalchemy import func

    # Count by status
    status_counts = (
        await session.execute(select(Alarm.status, func.count(Alarm.id)).group_by(Alarm.status))
    ).all()

    # Count by severity
    severity_counts = (
        await session.execute(select(Alarm.severity, func.count(Alarm.id)).group_by(Alarm.severity))
    ).all()

    # Total count
    total = await session.scalar(select(func.count(Alarm.id)))

    return {
        "total": total or 0,
        "by_status": {str(s): c for s, c in status_counts},
        "by_severity": {s: c for s, c in severity_counts},
    }


@router.post("/bulk/ack", response_model=BulkOperationOut)
async def bulk_ack_alarms(
    request: Request,
    body: BulkAckIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    alarms = (await session.scalars(select(Alarm).where(Alarm.id.in_(body.alarm_ids)))).all()
    by_id = {alarm.id: alarm for alarm in alarms}

    changed = 0
    unchanged = 0
    missing: list[uuid.UUID] = []
    redis = get_redis(request)

    for alarm_id in body.alarm_ids:
        alarm = by_id.get(alarm_id)
        if alarm is None:
            missing.append(alarm_id)
            continue

        was_changed = await acknowledge_alarm(
            session,
            alarm,
            acked_by=body.acked_by,
            note=body.note,
        )
        if was_changed:
            changed += 1
            await enqueue_alarm_acked_event(
                redis,
                alarm_id=alarm.id,
                acked_by=body.acked_by,
                note=body.note,
                logger=logger,
            )
            await enqueue_alarm_state_changed_event(
                redis,
                alarm_id=alarm.id,
                state=alarm.status.value,
                logger=logger,
            )
        else:
            unchanged += 1

    return BulkOperationOut(
        requested=len(body.alarm_ids),
        changed=changed,
        unchanged=unchanged,
        missing=missing,
    )


@router.post("/bulk/resolve", response_model=BulkOperationOut)
async def bulk_resolve_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    alarms = (await session.scalars(select(Alarm).where(Alarm.id.in_(body.alarm_ids)))).all()
    by_id = {alarm.id: alarm for alarm in alarms}

    changed = 0
    unchanged = 0
    missing: list[uuid.UUID] = []
    redis = get_redis(request)

    for alarm_id in body.alarm_ids:
        alarm = by_id.get(alarm_id)
        if alarm is None:
            missing.append(alarm_id)
            continue
        try:
            was_changed = await transition_alarm(
                session,
                alarm,
                target_status=AlarmStatus.RESOLVED,
                actor=body.actor,
                note=body.note,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                unchanged += 1
                continue
            raise
        if was_changed:
            changed += 1
            await enqueue_alarm_state_changed_event(
                redis,
                alarm_id=alarm.id,
                state=alarm.status.value,
                logger=logger,
            )
        else:
            unchanged += 1

    return BulkOperationOut(
        requested=len(body.alarm_ids),
        changed=changed,
        unchanged=unchanged,
        missing=missing,
    )


@router.post("/bulk/cancel", response_model=BulkOperationOut)
async def bulk_cancel_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    alarms = (await session.scalars(select(Alarm).where(Alarm.id.in_(body.alarm_ids)))).all()
    by_id = {alarm.id: alarm for alarm in alarms}

    changed = 0
    unchanged = 0
    missing: list[uuid.UUID] = []
    redis = get_redis(request)

    for alarm_id in body.alarm_ids:
        alarm = by_id.get(alarm_id)
        if alarm is None:
            missing.append(alarm_id)
            continue
        try:
            was_changed = await transition_alarm(
                session,
                alarm,
                target_status=AlarmStatus.CANCELLED,
                actor=body.actor,
                note=body.note,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                unchanged += 1
                continue
            raise
        if was_changed:
            changed += 1
            await enqueue_alarm_state_changed_event(
                redis,
                alarm_id=alarm.id,
                state=alarm.status.value,
                logger=logger,
            )
        else:
            unchanged += 1

    return BulkOperationOut(
        requested=len(body.alarm_ids),
        changed=changed,
        unchanged=unchanged,
        missing=missing,
    )


@router.get("/{alarm_id}", response_model=AlarmOut)
async def get_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AlarmOut:
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)
    return AlarmOut.model_validate(alarm, from_attributes=True)


@router.post("/{alarm_id}/ack")
async def ack_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: AckIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await acknowledge_alarm(session, alarm, acked_by=body.acked_by, note=body.note)

    request.state.alarm_id = str(alarm.id)
    if changed:
        redis = get_redis(request)
        await enqueue_alarm_acked_event(
            redis,
            alarm_id=alarm.id,
            acked_by=body.acked_by,
            note=body.note,
            logger=logger,
        )
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{alarm_id}/resolve")
async def resolve_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await transition_alarm(
        session,
        alarm,
        target_status=AlarmStatus.RESOLVED,
        actor=body.actor,
        note=body.note,
    )
    request.state.alarm_id = str(alarm.id)
    if changed:
        redis = get_redis(request)
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{alarm_id}/cancel")
async def cancel_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await transition_alarm(
        session,
        alarm,
        target_status=AlarmStatus.CANCELLED,
        actor=body.actor,
        note=body.note,
    )
    request.state.alarm_id = str(alarm.id)
    if changed:
        redis = get_redis(request)
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Alarm Notes endpoints
@router.get("/{alarm_id}/notes", response_model=list[AlarmNoteOut])
async def list_alarm_notes(
    alarm_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[AlarmNoteOut]:
    """List all notes for an alarm.

    Args:
        alarm_id: Alarm UUID
        session: Database session

    Returns:
        List of alarm notes ordered by creation time
    """
    # Verify alarm exists
    await get_alarm_or_404(session, alarm_id)

    notes = (
        await session.scalars(
            select(AlarmNote)
            .where(AlarmNote.alarm_id == alarm_id)
            .order_by(AlarmNote.created_at.asc())
        )
    ).all()

    return [AlarmNoteOut.model_validate(note, from_attributes=True) for note in notes]


@router.post("/{alarm_id}/notes", response_model=AlarmNoteOut, status_code=status.HTTP_201_CREATED)
async def create_alarm_note(
    alarm_id: uuid.UUID,
    body: AlarmNoteIn,
    session: AsyncSession = Depends(get_session),
) -> AlarmNoteOut:
    """Create a note for an alarm.

    Args:
        alarm_id: Alarm UUID
        body: Note content
        session: Database session

    Returns:
        Created note
    """
    # Verify alarm exists
    await get_alarm_or_404(session, alarm_id)

    note = AlarmNote(
        alarm_id=alarm_id,
        note=body.note,
        created_by=body.created_by,
        note_type="manual",
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)

    return AlarmNoteOut.model_validate(note, from_attributes=True)
