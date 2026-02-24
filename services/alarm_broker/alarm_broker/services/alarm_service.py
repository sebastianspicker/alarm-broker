from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import Alarm, AlarmStatus

_ALLOWED_TRANSITIONS: dict[AlarmStatus, set[AlarmStatus]] = {
    AlarmStatus.TRIGGERED: {
        AlarmStatus.ACKNOWLEDGED,
        AlarmStatus.RESOLVED,
        AlarmStatus.CANCELLED,
    },
    AlarmStatus.ACKNOWLEDGED: {
        AlarmStatus.RESOLVED,
        AlarmStatus.CANCELLED,
    },
    AlarmStatus.RESOLVED: set(),
    AlarmStatus.CANCELLED: set(),
}


def _merge_meta_note(alarm: Alarm, key: str, note: str | None) -> None:
    if note:
        alarm.meta = {**(alarm.meta or {}), key: note}


async def get_alarm_by_ack_token(session: AsyncSession, ack_token: str) -> Alarm | None:
    return await session.scalar(select(Alarm).where(Alarm.ack_token == ack_token))


async def acknowledge_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    acked_by: str | None = None,
    note: str | None = None,
) -> bool:
    if alarm.status != AlarmStatus.TRIGGERED:
        return False

    alarm.status = AlarmStatus.ACKNOWLEDGED
    alarm.acked_at = datetime.now(UTC)
    alarm.acked_by = acked_by
    _merge_meta_note(alarm, "ack_note", note)
    await session.commit()
    return True


async def transition_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    target_status: AlarmStatus,
    actor: str | None = None,
    note: str | None = None,
) -> bool:
    current = alarm.status
    if current == target_status:
        return False

    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invalid status transition: {current.value} -> {target_status.value}",
        )

    now = datetime.now(UTC)
    alarm.status = target_status

    if target_status == AlarmStatus.RESOLVED:
        alarm.resolved_at = now
        alarm.resolved_by = actor
        _merge_meta_note(alarm, "resolve_note", note)
    elif target_status == AlarmStatus.CANCELLED:
        alarm.cancelled_at = now
        alarm.cancelled_by = actor
        _merge_meta_note(alarm, "cancel_note", note)

    await session.commit()
    return True


async def get_alarm_or_404(session: AsyncSession, alarm_id: uuid.UUID | str) -> Alarm:
    alarm = await session.get(Alarm, alarm_id)
    if not alarm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return alarm
