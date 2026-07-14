from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker import constants
from alarm_broker.core.metrics import record_event
from alarm_broker.db.models import AlarmEventOutbox
from alarm_broker.services.event_publisher import EventPublisher


@dataclass(frozen=True)
class EventResult:
    """Result of an event enqueue operation."""

    success: bool
    error: str | None = None


async def enqueue_alarm_acked_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    acked_by: str | None,
    note: str | None,
    logger: logging.Logger,
) -> EventResult:
    """Enqueue an alarm acknowledged event.

    Convenience wrapper around EventPublisher with error handling and metrics.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        acked_by: Who acknowledged the alarm
        note: Optional note
        logger: Logger instance

    Returns:
        EventResult indicating success or failure with error detail
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_acknowledged(
            alarm_id=str(alarm_id),
            acknowledged_by=acked_by or "unknown",
            note=note,
        )
        record_event("alarm_acked_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception("enqueue alarm_acked failed", extra={"alarm_id": str(alarm_id)})
        return EventResult(success=False, error=str(exc))


async def enqueue_alarm_created_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    logger: logging.Logger,
) -> EventResult:
    """Enqueue an alarm created event.

    Convenience wrapper around EventPublisher with error handling and metrics.
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_created(alarm_id=str(alarm_id))
        record_event("alarm_created_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception("enqueue alarm_created failed", extra={"alarm_id": str(alarm_id)})
        return EventResult(success=False, error=str(exc))


async def enqueue_alarm_state_changed_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    state: str,
    logger: logging.Logger,
    old_state: str = "unknown",
) -> EventResult:
    """Enqueue an alarm state changed event.

    Convenience wrapper around EventPublisher with error handling and metrics.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        state: New state
        logger: Logger instance

    Returns:
        EventResult indicating success or failure with error detail
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_state_changed(
            alarm_id=str(alarm_id),
            old_state=old_state,
            new_state=state,
        )
        record_event("alarm_state_changed_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception(
            "enqueue alarm_state_changed failed",
            extra={"alarm_id": str(alarm_id), "state": state},
        )
        return EventResult(success=False, error=str(exc))


async def _enqueue_outbox_event(
    redis: Any, event: AlarmEventOutbox, logger: logging.Logger
) -> EventResult:
    payload = event.payload or {}
    if event.event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        acknowledged_by = payload.get("acknowledged_by")
        note = payload.get("note")
        return await enqueue_alarm_acked_event(
            redis,
            alarm_id=event.alarm_id,
            acked_by=str(acknowledged_by) if acknowledged_by else None,
            note=str(note) if note else None,
            logger=logger,
        )
    if event.event_type == constants.EVENT_ALARM_STATE_CHANGED:
        new_state = payload.get("new_state")
        old_state = payload.get("old_state")
        if not new_state:
            return EventResult(success=False, error="outbox state event has no new_state")
        return await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=event.alarm_id,
            state=str(new_state),
            old_state=str(old_state) if old_state else "unknown",
            logger=logger,
        )
    return EventResult(success=False, error=f"unsupported outbox event type: {event.event_type}")


async def dispatch_pending_alarm_events(
    session: AsyncSession,
    redis: Any,
    *,
    logger: logging.Logger,
    alarm_id: uuid.UUID | None = None,
    limit: int = 500,
) -> int:
    """Publish pending lifecycle events and durably record accepted queue writes."""
    stmt = (
        select(AlarmEventOutbox)
        .where(AlarmEventOutbox.published_at.is_(None))
        .order_by(AlarmEventOutbox.created_at, AlarmEventOutbox.sequence, AlarmEventOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if alarm_id is not None:
        stmt = stmt.where(AlarmEventOutbox.alarm_id == alarm_id)
    events = list((await session.scalars(stmt)).all())

    published = 0
    blocked_alarms: set[uuid.UUID] = set()
    for event in events:
        if event.alarm_id in blocked_alarms:
            continue
        result = await _enqueue_outbox_event(redis, event, logger)
        event.attempts += 1
        event.last_error = result.error
        if result.success:
            event.published_at = datetime.now(UTC)
            published += 1
        else:
            blocked_alarms.add(event.alarm_id)
            logger.warning(
                "alarm_event_outbox_publish_failed",
                extra={
                    "alarm_id": str(event.alarm_id),
                    "outbox_id": str(event.id),
                    "event_type": event.event_type,
                    "error": result.error,
                },
            )
    await session.commit()
    return published


async def has_pending_alarm_events(session: AsyncSession, alarm_id: uuid.UUID) -> bool:
    """Return whether durable lifecycle events still await queue acceptance."""
    pending_id = await session.scalar(
        select(AlarmEventOutbox.id)
        .where(
            AlarmEventOutbox.alarm_id == alarm_id,
            AlarmEventOutbox.published_at.is_(None),
        )
        .limit(1)
    )
    return pending_id is not None
