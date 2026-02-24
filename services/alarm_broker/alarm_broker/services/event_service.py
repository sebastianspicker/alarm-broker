from __future__ import annotations

import logging
import uuid
from typing import Any

from alarm_broker.core.metrics import record_event


async def enqueue_alarm_acked_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    acked_by: str | None,
    note: str | None,
    logger: logging.Logger,
) -> bool:
    try:
        await redis.enqueue_job("alarm_acked", str(alarm_id), acked_by, note)
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
    try:
        await redis.enqueue_job("alarm_state_changed", str(alarm_id), state)
        record_event("alarm_state_changed_enqueued")
        return True
    except Exception:
        logger.exception(
            "enqueue alarm_state_changed failed",
            extra={"alarm_id": str(alarm_id), "state": state},
        )
        return False
