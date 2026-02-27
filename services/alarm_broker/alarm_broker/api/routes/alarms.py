from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_redis, get_session, require_admin
from alarm_broker.api.schemas import (
    AckIn,
    AlarmNoteIn,
    AlarmNoteOut,
    AlarmOut,
    AlarmPatchSchema,
    BulkAckIn,
    BulkOperationOut,
    BulkTransitionIn,
    ExportFormat,
    TransitionIn,
)
from alarm_broker.core.errors import (
    ConflictError,
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

T = TypeVar("T")


async def _execute_bulk_operation(
    session: AsyncSession,
    redis: ArqRedis,
    alarm_ids: list[uuid.UUID],
    process_alarm: Callable[[Alarm], Coroutine[Any, Any, bool]],
    after_change: Callable[[Alarm], Coroutine[Any, Any, None]] | None = None,
) -> BulkOperationOut:
    """Execute a bulk operation on alarms with common pattern.

    Args:
        session: Database session
        redis: Redis connection
        alarm_ids: List of alarm IDs to process
        process_alarm: Async function to process each alarm, returns True if changed
        after_change: Optional async function called after each successful change

    Returns:
        BulkOperationOut with counts
    """
    alarms = (await session.scalars(select(Alarm).where(Alarm.id.in_(alarm_ids)))).all()
    by_id = {alarm.id: alarm for alarm in alarms}

    changed = 0
    unchanged = 0
    missing: list[uuid.UUID] = []

    for alarm_id in alarm_ids:
        alarm = by_id.get(alarm_id)
        if alarm is None:
            missing.append(alarm_id)
            continue

        try:
            was_changed = await process_alarm(alarm)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                unchanged += 1
                continue
            raise
        except ConflictError:
            # ConflictError is raised by acknowledge_alarm when alarm is not in TRIGGERED status
            unchanged += 1
            continue

        if was_changed:
            changed += 1
            if after_change:
                await after_change(alarm)
        else:
            unchanged += 1

    return BulkOperationOut(
        requested=len(alarm_ids),
        changed=changed,
        unchanged=unchanged,
        missing=missing,
    )


class SortOrder(StrEnum):
    """Sort order for alarm listing."""

    DESC = "desc"
    ASC = "asc"


class SortField(StrEnum):
    """Fields available for sorting alarms."""

    CREATED_AT = "created_at"
    STATUS = "status"
    SEVERITY = "severity"


def _apply_alarm_filters(
    stmt,
    status=None,
    severity=None,
    person_id=None,
    room_id=None,
    site_id=None,
    device_id=None,
    source=None,
    created_after=None,
    created_before=None,
):
    """Apply common filter parameters to an alarm query.

    Args:
        stmt: SQLAlchemy select statement
        status: Filter by alarm status
        severity: Filter by severity level
        person_id: Filter by person ID
        room_id: Filter by room ID
        site_id: Filter by site ID
        device_id: Filter by device ID
        source: Filter by trigger source
        created_after: Filter alarms created after this datetime
        created_before: Filter alarms created before this datetime

    Returns:
        Updated select statement with filters applied
    """
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

    return stmt


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
    stmt = _apply_alarm_filters(
        stmt,
        status=status,
        severity=severity,
        person_id=person_id,
        room_id=room_id,
        site_id=site_id,
        device_id=device_id,
        source=source,
        created_after=created_after,
        created_before=created_before,
    )

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


@router.get("/export")
async def export_alarms(
    response: Response,
    # Filtering
    status: AlarmStatus | None = Query(default=None),
    severity: str | None = Query(default=None),
    person_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    source: str | None = Query(default=None),
    # Date range filtering
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    # Export options
    format: ExportFormat = Query(default=ExportFormat.JSON),
    limit: int = Query(default=1000, ge=1, le=10000),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export alarms in JSON or CSV format.

    Args:
        response: FastAPI response for setting content disposition
        status: Filter by alarm status
        severity: Filter by severity level
        person_id: Filter by person ID
        room_id: Filter by room ID
        site_id: Filter by site ID
        device_id: Filter by device ID
        source: Filter by trigger source
        created_after: Filter alarms created after this datetime
        created_before: Filter alarms created before this datetime
        format: Export format (json or csv)
        limit: Maximum number of results (1-10000)
        session: Database session

    Returns:
        StreamingResponse with exported alarms
    """
    stmt = select(Alarm)

    # Apply filters using shared helper
    stmt = _apply_alarm_filters(
        stmt,
        status=status,
        severity=severity,
        person_id=person_id,
        room_id=room_id,
        site_id=site_id,
        device_id=device_id,
        source=source,
        created_after=created_after,
        created_before=created_before,
    )

    # Apply sorting and limit
    stmt = stmt.order_by(Alarm.created_at.desc()).limit(limit)
    alarms = list((await session.scalars(stmt)).all())

    # Export as JSON
    if format == ExportFormat.JSON:
        data = [
            AlarmOut.model_validate(alarm, from_attributes=True).model_dump(mode="json")
            for alarm in alarms
        ]
        content = json.dumps(data, indent=2, default=str)
        media_type = "application/json"
        filename = f"alarms_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # Export as CSV
    else:
        output = io.StringIO()
        if alarms:
            # Get field names from first alarm
            field_names = [
                "id",
                "status",
                "source",
                "event",
                "created_at",
                "person_id",
                "room_id",
                "site_id",
                "device_id",
                "severity",
                "silent",
                "zammad_ticket_id",
                "ack_token",
                "acked_at",
                "acked_by",
                "resolved_at",
                "resolved_by",
                "cancelled_at",
                "cancelled_by",
            ]
            writer = csv.DictWriter(output, fieldnames=field_names, extrasaction="ignore")
            writer.writeheader()
            for alarm in alarms:
                row = {k: getattr(alarm, k, None) for k in field_names}
                # Convert datetime to ISO format
                for dt_field in ["created_at", "acked_at", "resolved_at", "cancelled_at"]:
                    if row[dt_field]:
                        row[dt_field] = row[dt_field].isoformat()
                writer.writerow(row)
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"alarms_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(iter([content]), media_type=media_type)


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


async def _execute_bulk_state_transition(
    alarm_ids: list[uuid.UUID],
    target_status: AlarmStatus,
    request: Request,
    session: AsyncSession,
    actor_or_acked_by: str | None,
    note: str | None,
    is_ack: bool = False,
) -> BulkOperationOut:
    """Execute a bulk state transition on alarms.

    Args:
        alarm_ids: List of alarm UUIDs to process
        target_status: The target AlarmStatus to transition to
        request: FastAPI request object (for Redis)
        session: Database session
        actor_or_acked_by: Actor or user who triggered the change
        note: Optional note for the transition
        is_ack: If True, use acknowledge_alarm instead of transition_alarm

    Returns:
        BulkOperationOut with counts
    """
    redis = get_redis(request)

    async def process(alarm: Alarm) -> bool:
        if is_ack:
            return await acknowledge_alarm(
                session,
                alarm,
                acked_by=actor_or_acked_by,
                note=note,
            )
        return await transition_alarm(
            session,
            alarm,
            target_status=target_status,
            actor=actor_or_acked_by,
            note=note,
        )

    async def after_change(alarm: Alarm) -> None:
        if is_ack:
            await enqueue_alarm_acked_event(
                redis,
                alarm_id=alarm.id,
                acked_by=actor_or_acked_by,
                note=note,
                logger=logger,
            )
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )

    return await _execute_bulk_operation(
        session,
        redis,
        alarm_ids,
        process,
        after_change,
    )


async def _execute_single_state_transition(
    request: Request,
    session: AsyncSession,
    alarm_id: uuid.UUID,
    *,
    target_status: AlarmStatus,
    actor_or_acked_by: str | None,
    note: str | None,
    is_ack: bool = False,
) -> Response:
    """Execute one alarm state transition and enqueue follow-up events."""
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)

    if is_ack:
        changed = await acknowledge_alarm(session, alarm, acked_by=actor_or_acked_by, note=note)
    else:
        changed = await transition_alarm(
            session,
            alarm,
            target_status=target_status,
            actor=actor_or_acked_by,
            note=note,
        )

    if changed:
        redis = get_redis(request)
        if is_ack:
            await enqueue_alarm_acked_event(
                redis,
                alarm_id=alarm.id,
                acked_by=actor_or_acked_by,
                note=note,
                logger=logger,
            )
        await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk/ack", response_model=BulkOperationOut)
async def bulk_ack_alarms(
    request: Request,
    body: BulkAckIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Acknowledge multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.ACKNOWLEDGED,
        request=request,
        session=session,
        actor_or_acked_by=body.acked_by,
        note=body.note,
        is_ack=True,
    )


@router.post("/bulk/resolve", response_model=BulkOperationOut)
async def bulk_resolve_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Resolve multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.RESOLVED,
        request=request,
        session=session,
        actor_or_acked_by=body.actor,
        note=body.note,
        is_ack=False,
    )


@router.post("/bulk/cancel", response_model=BulkOperationOut)
async def bulk_cancel_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Cancel multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.CANCELLED,
        request=request,
        session=session,
        actor_or_acked_by=body.actor,
        note=body.note,
        is_ack=False,
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


@router.patch("/{alarm_id}", response_model=AlarmOut)
async def patch_alarm(
    alarm_id: uuid.UUID,
    patch: AlarmPatchSchema,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AlarmOut:
    """Teilweises Update eines Alarms.

    Aktualisiert nur die im Request-Body angegebenen Felder.
    None-Werte werden ignoriert und nicht auf None gesetzt.
    """
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)

    # Aktualisiere nur Felder, die im Patch enthalten sind (nicht None)
    patch_data = patch.model_dump(exclude_none=True)

    if "title" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "title": patch_data["title"]}

    if "description" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "description": patch_data["description"]}

    if "severity" in patch_data:
        alarm.severity = patch_data["severity"]

    if "tags" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "tags": patch_data["tags"]}

    await session.commit()
    await session.refresh(alarm)

    return AlarmOut.model_validate(alarm, from_attributes=True)


@router.post("/{alarm_id}/ack")
async def ack_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: AckIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.ACKNOWLEDGED,
        actor_or_acked_by=body.acked_by,
        note=body.note,
        is_ack=True,
    )


@router.post("/{alarm_id}/resolve")
async def resolve_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.RESOLVED,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


@router.post("/{alarm_id}/cancel")
async def cancel_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.CANCELLED,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


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
    request: Request,
    x_admin_email: str | None = Header(default=None, alias="X-Admin-Email"),
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
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)
    created_by = body.created_by or x_admin_email or "admin"

    note = AlarmNote(
        alarm_id=alarm.id,
        note=body.note,
        created_by=created_by,
        note_type="manual",
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)

    return AlarmNoteOut.model_validate(note, from_attributes=True)


@router.delete("/{alarm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Löscht einen Alarm (Soft-Delete).

    Args:
        alarm_id: Alarm UUID
        request: FastAPI request object (for Redis)
        session: Database session

    Returns:
        204 No Content
    """
    from alarm_broker.api.deps import get_app_settings

    # Alarm finden
    alarm = await get_alarm_or_404(session, alarm_id)

    # Bereits gelöscht?
    if alarm.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Alarm wurde bereits gelöscht"
        )

    # Soft-Delete: Setze deleted_at Timestamp
    alarm.deleted_at = datetime.now()

    # Admin-Key als deleted_by verwenden (aus dem Header)
    settings = get_app_settings(request)
    if settings.admin_api_key:
        # Verwende einen kurzen Hash des Admin-Keys als Identifikation
        alarm.deleted_by = f"admin:{settings.admin_api_key[:8]}..."

    await session.commit()
