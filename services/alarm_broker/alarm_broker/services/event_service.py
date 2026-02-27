from __future__ import annotations

import logging
import uuid
from typing import Any

from alarm_broker.core.metrics import record_event
from alarm_broker.services.event_publisher import EventPublisher


async def enqueue_alarm_acked_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    acked_by: str | None,
    note: str | None,
    logger: logging.Logger,
) -> bool:
    """Enqueue an alarm acknowledged event.

    This function is deprecated. Use EventPublisher.publish_alarm_acknowledged() instead.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        acked_by: Who acknowledged the alarm
        note: Optional note
        logger: Logger instance

    Returns:
        True if enqueued successfully
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_acknowledged(
            alarm_id=str(alarm_id),
            acknowledged_by=acked_by or "unknown",
            note=note,
        )
        record_event("alarm_acked_enqueued")
        return True
    except Exception:
        logger.exception("enqueue alarm_acked failed", extra={"alarm_id": str(alarm_id)})
        return False


async def enqueue_alarm_state_changed_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    state: str,
    logger: logging.Logger,
) -> bool:
    """Enqueue an alarm state changed event.

    This function is deprecated. Use EventPublisher.publish_alarm_state_changed() instead.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        state: New state
        logger: Logger instance

    Returns:
        True if enqueued successfully
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_state_changed(
            alarm_id=str(alarm_id),
            old_state="unknown",
            new_state=state,
        )
        record_event("alarm_state_changed_enqueued")
        return True
    except Exception:
        logger.exception(
            "enqueue alarm_state_changed failed",
            extra={"alarm_id": str(alarm_id), "state": state},
        )
        return False
