"""Background worker tasks for alarm processing.

This module contains the arq worker tasks that handle:
- Initial alarm processing and notification fan-out
- Escalation scheduling and execution
- ACK notification handling
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker import constants
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.core.metrics import record_event
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.services.enrichment_service import enrich_alarm_context
from alarm_broker.services.notification_service import NotificationService, log_notification

logger = logging.getLogger("alarm_broker")


async def process_alarm_event(ctx: dict, payload: dict[str, Any]) -> None:
    """Process a generic alarm event from EventPublisher.

    This is a unified entry point for all alarm events. It dispatches
    to the appropriate existing tasks based on the event type.

    Args:
        ctx: Worker context dictionary
        payload: Event payload containing event_type and event data
    """
    event_type = payload.get("event_type")
    alarm_id = payload.get("alarm_id")

    if not event_type or not alarm_id:
        logger.warning("process_alarm_event_invalid_payload", extra={"payload": payload})
        return

    # Dispatch to appropriate handler based on event type
    if event_type == constants.EVENT_ALARM_CREATED:
        await alarm_created(ctx, str(alarm_id))
    elif event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        acked_by = payload.get("acknowledged_by")
        note = payload.get("note")
        await alarm_acked(ctx, str(alarm_id), acked_by, note)
    elif event_type == constants.EVENT_ALARM_STATE_CHANGED:
        state = payload.get("new_state")
        await alarm_state_changed(ctx, str(alarm_id), state)
    elif event_type == constants.EVENT_ALARM_RESOLVED:
        # For resolved events, we could add additional handling
        logger.info("alarm_resolved_event_received", extra={"alarm_id": str(alarm_id)})
    elif event_type == constants.EVENT_ALARM_CANCELLED:
        # For cancelled events, we could add additional handling
        logger.info("alarm_cancelled_event_received", extra={"alarm_id": str(alarm_id)})
    else:
        logger.warning(
            "process_alarm_event_unknown_type",
            extra={"event_type": event_type, "alarm_id": str(alarm_id)},
        )


def _get_notification_service(ctx: dict) -> NotificationService:
    """Get notification service from worker context.

    Args:
        ctx: Worker context dictionary

    Returns:
        NotificationService instance
    """
    return NotificationService(
        zammad=ctx["zammad"],
        sendxms=ctx["sendxms"],
        signal=ctx["signal"],
    )


async def alarm_created(ctx: dict, alarm_id: str) -> None:
    """Process a newly created alarm.

    This task is enqueued when an alarm is triggered. It:
    1. Enriches the alarm context
    2. Creates a Zammad ticket (if configured)
    3. Sends stage 0 notifications
    4. Schedules escalation steps

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
    """
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    settings = ctx["settings"]
    notification = _get_notification_service(ctx)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id})
            return

        enriched = await enrich_alarm_context(session, alarm)
        ack_url = f"{settings.base_url}/a/{alarm.ack_token}"

        # Create Zammad ticket
        ticket_id = await notification.handle_zammad_ticket(
            session, alarm, enriched, ack_url, settings
        )
        if ticket_id:
            alarm.zammad_ticket_id = ticket_id
            await session.commit()

        # Send stage 0 notifications
        await notification.send_escalation_step(
            session, alarm, enriched, step_no=0, ack_url=ack_url
        )

        # Schedule future escalation steps
        schedule = await notification.get_escalation_schedule(session)
        for step_no, after_seconds in schedule:
            await ctx["redis"].enqueue_job(
                "escalate", alarm_id, step_no, _defer_by=int(after_seconds)
            )
            logger.info(
                "escalation_scheduled",
                extra={"alarm_id": alarm_id, "step_no": step_no, "after_seconds": after_seconds},
            )


async def escalate(ctx: dict, alarm_id: str, step_no: int) -> None:
    """Execute an escalation step.

    This task is scheduled by alarm_created for future execution.
    It only sends notifications if the alarm is still in triggered state.

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
        step_no: Escalation step number to execute
    """
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    settings = ctx["settings"]
    notification = _get_notification_service(ctx)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id, "step_no": step_no})
            return

        if alarm.status != AlarmStatus.TRIGGERED:
            logger.info(
                "escalation_skipped",
                extra={
                    "alarm_id": alarm_id,
                    "step_no": step_no,
                    "status": alarm.status.value,
                },
            )
            return

        enriched = await enrich_alarm_context(session, alarm)
        ack_url = f"{settings.base_url}/a/{alarm.ack_token}"

        await notification.send_escalation_step(
            session, alarm, enriched, step_no=step_no, ack_url=ack_url
        )

        logger.info(
            "escalation_completed",
            extra={"alarm_id": alarm_id, "step_no": step_no},
        )


async def alarm_acked(
    ctx: dict, alarm_id: str, acked_by: str | None = None, note: str | None = None
) -> None:
    """Handle alarm acknowledgment.

    This task is enqueued when an alarm is acknowledged. It updates
    the Zammad ticket with an internal note.

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
        acked_by: Name of the person who acknowledged
        note: Optional note from the acknowledger
    """
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    zammad: ZammadClient = ctx["zammad"]

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id})
            return

        if not alarm.zammad_ticket_id:
            logger.info(
                "ack_no_zammad_ticket",
                extra={"alarm_id": alarm_id},
            )
            return

        if not zammad.enabled():
            logger.debug("zammad_disabled", extra={"alarm_id": alarm_id})
            return

        acked_at = alarm.acked_at or datetime.now(UTC)

        notification = _get_notification_service(ctx)
        success = await notification.add_zammad_ack_note(
            session,
            alarm_id=alarm.id,
            ticket_id=alarm.zammad_ticket_id,
            acked_by=acked_by,
            acked_at=acked_at,
            note=note,
        )

        if success:
            logger.info(
                "ack_note_added",
                extra={"alarm_id": alarm_id, "ticket_id": alarm.zammad_ticket_id},
            )
        else:
            logger.warning(
                "ack_note_failed",
                extra={"alarm_id": alarm_id, "ticket_id": alarm.zammad_ticket_id},
            )


async def alarm_state_changed(ctx: dict, alarm_id: str, state: str) -> None:
    """Send state-change webhook callbacks with retry and audit logging.

    Args:
        ctx: Worker context with settings, sessionmaker and HTTP client
        alarm_id: UUID string of the alarm
        state: New alarm state value
    """
    settings = ctx["settings"]
    webhook_url = getattr(settings, "webhook_url", None)
    webhook_enabled = getattr(settings, "webhook_enabled", False)
    if not webhook_enabled or not webhook_url:
        return

    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    http = ctx["http"]

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning(
                "alarm_not_found",
                extra={"alarm_id": alarm_id, "state": state, "channel": "webhook"},
            )
            return

        payload = _build_webhook_payload(alarm, state)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.webhook_secret:
            headers["X-Webhook-Secret"] = settings.webhook_secret

        await _send_webhook_with_retry(
            http=http,
            webhook_url=webhook_url,
            payload=payload,
            headers=headers,
            timeout=settings.webhook_timeout_seconds,
            alarm_id=alarm.id,
            session=session,
            state=state,
        )


def _build_webhook_payload(alarm: Alarm, state: str) -> dict[str, Any]:
    """Build the webhook payload from an alarm.

    Args:
        alarm: Alarm instance
        state: New alarm state value

    Returns:
        Dictionary payload for webhook
    """
    return {
        "event": constants.EVENT_ALARM_STATE_CHANGED,
        "alarm_id": str(alarm.id),
        "state": state,
        "created_at": alarm.created_at.isoformat() if alarm.created_at else None,
        "acked_at": alarm.acked_at.isoformat() if alarm.acked_at else None,
        "resolved_at": alarm.resolved_at.isoformat() if alarm.resolved_at else None,
        "cancelled_at": alarm.cancelled_at.isoformat() if alarm.cancelled_at else None,
        "person_id": alarm.person_id,
        "room_id": alarm.room_id,
        "site_id": alarm.site_id,
        "device_id": alarm.device_id,
    }


async def _send_webhook_with_retry(
    http: Any,
    webhook_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    alarm_id: uuid.UUID,
    session: AsyncSession,
    state: str,
    max_attempts: int = 3,
) -> None:
    """Send webhook with exponential backoff retry.

    Args:
        http: HTTP client
        webhook_url: URL to send webhook to
        payload: JSON payload
        headers: HTTP headers
        timeout: Request timeout in seconds
        alarm_id: Alarm ID for logging
        session: Database session
        state: State for logging
        max_attempts: Maximum retry attempts
    """
    for attempt in range(1, max_attempts + 1):
        try:
            response = await http.post(
                webhook_url,
                json=payload,
                headers=headers,
                timeout=float(timeout),
            )
            response.raise_for_status()
            await log_notification(
                session,
                alarm_id=alarm_id,
                channel="webhook",
                target_id=None,
                payload={"state": state, "attempt": attempt},
                result="ok",
            )
            record_event("webhook_delivery_ok")
            return
        except Exception as exc:
            is_last = attempt == max_attempts
            if is_last:
                logger.exception(
                    "webhook_delivery_failed",
                    extra={
                        "alarm_id": str(alarm_id),
                        "state": state,
                        "attempts": max_attempts,
                        "error": str(exc),
                    },
                )
                await log_notification(
                    session,
                    alarm_id=alarm_id,
                    channel="webhook",
                    target_id=None,
                    payload={"state": state, "attempts": max_attempts},
                    result="error",
                    error=str(exc),
                )
                record_event("webhook_delivery_error")
                return
            await asyncio.sleep(0.2 * attempt)
