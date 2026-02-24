"""Background worker tasks for alarm processing.

This module contains the arq worker tasks that handle:
- Initial alarm processing and notification fan-out
- Escalation scheduling and execution
- ACK notification handling
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.connectors.sendxms import SendXmsClient
from alarm_broker.connectors.signal import SignalClient
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.services.enrichment_service import enrich_alarm_context
from alarm_broker.services.notification_service import NotificationService

logger = logging.getLogger("alarm_broker")


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
