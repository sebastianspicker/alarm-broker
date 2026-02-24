"""Notification service for handling external communications.

This service encapsulates the logic for sending notifications through
various channels (Zammad, SMS, Signal) and logging the results.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from alarm_broker.connectors.sendxms import SendXmsClient
from alarm_broker.connectors.signal import SignalClient
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.db.models import Alarm, AlarmNotification, EscalationStep
from alarm_broker.worker.message import format_alarm_message

logger = logging.getLogger("alarm_broker")


async def log_notification(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload: dict[str, Any],
    result: str,
    error: str | None = None,
) -> None:
    """Log a notification attempt to the database.

    Args:
        session: Database session
        alarm_id: ID of the alarm
        channel: Notification channel (zammad, sms, signal)
        target_id: ID of the escalation target (if applicable)
        payload: Payload sent to the channel
        result: Result of the notification (ok, error)
        error: Error message if result is error
    """
    session.add(
        AlarmNotification(
            alarm_id=alarm_id,
            channel=channel,
            target_id=target_id,
            payload=payload,
            result=result,
            error=error,
        )
    )
    await session.commit()


class NotificationService:
    """Service for sending alarm notifications through various channels.

    This class encapsulates all notification logic, making it easier to
    test, extend, and maintain the notification flow.
    """

    def __init__(
        self,
        zammad: ZammadClient,
        sendxms: SendXmsClient,
        signal: SignalClient,
    ) -> None:
        """Initialize the notification service.

        Args:
            zammad: Zammad client for ticket management
            sendxms: SMS client for text messages
            signal: Signal client for group messages
        """
        self._zammad = zammad
        self._sendxms = sendxms
        self._signal = signal

    async def handle_zammad_ticket(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: dict[str, Any],
        ack_url: str,
        settings: Any,
    ) -> int | None:
        """Create a Zammad ticket for the alarm.

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            ack_url: ACK URL for responders
            settings: Application settings

        Returns:
            Ticket ID if created, None otherwise
        """
        if not self._zammad.enabled():
            return None

        payload = {
            "title": f"NOTFALLALARM – {enriched['person_name']} – {enriched['room_label']}",
            "group": settings.zammad_group,
            "priority_id": settings.zammad_priority_id_p0,
            "state_id": settings.zammad_state_id_new,
            "customer_id": settings.zammad_customer,
            "tags": ["notfall", "silent"],
            "article": {
                "subject": "Alarm ausgelöst (silent)",
                "body": format_alarm_message(
                    alarm_id=str(alarm.id),
                    person=str(enriched["person_name"]),
                    room=str(enriched["room_label"]),
                    site=str(enriched.get("site_name")) if enriched.get("site_name") else None,
                    created_at=alarm.created_at,
                    ack_url=ack_url,
                    step_no=0,
                ),
                "type": "note",
                "internal": True,
            },
        }

        try:
            ticket_id = await self._zammad.create_ticket(payload)
            await log_notification(
                session,
                alarm_id=alarm.id,
                channel="zammad",
                target_id=None,
                payload={"action": "create_ticket", "ticket_id": ticket_id},
                result="ok",
            )
            return ticket_id
        except Exception as e:
            logger.exception(
                "zammad_create_ticket_failed",
                extra={"alarm_id": str(alarm.id), "error": str(e)},
            )
            await log_notification(
                session,
                alarm_id=alarm.id,
                channel="zammad",
                target_id=None,
                payload={"action": "create_ticket"},
                result="error",
                error=str(e),
            )
            return None

    async def add_zammad_ack_note(
        self,
        session: AsyncSession,
        alarm_id: uuid.UUID,
        ticket_id: int,
        acked_by: str | None,
        acked_at: Any,
        note: str | None,
    ) -> bool:
        """Add an acknowledgment note to a Zammad ticket.

        Args:
            session: Database session
            alarm_id: Alarm ID for audit logging
            ticket_id: Zammad ticket ID
            acked_by: Person who acknowledged
            acked_at: Timestamp of acknowledgment
            note: Optional note from acknowledger

        Returns:
            True if successful, False otherwise
        """
        if not self._zammad.enabled():
            return False

        subject = "Alarm quittiert"
        body_parts = [
            f"ACK durch: {acked_by or '-'}",
            f"Zeit: {acked_at.isoformat()}",
        ]
        if note:
            body_parts.append(f"Notiz: {note}")
        body = "\n".join(body_parts)

        try:
            await self._zammad.add_internal_note(ticket_id, subject=subject, body=body)
            await log_notification(
                session,
                alarm_id=alarm_id,
                channel="zammad",
                target_id=None,
                payload={"action": "ack_update", "ticket_id": ticket_id},
                result="ok",
            )
            return True
        except Exception as e:
            logger.exception(
                "zammad_ack_note_failed",
                extra={"ticket_id": ticket_id, "error": str(e)},
            )
            return False

    async def send_escalation_step(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: dict[str, Any],
        *,
        step_no: int,
        ack_url: str,
        policy_id: str = "default",
    ) -> None:
        """Send notifications for an escalation step.

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            step_no: Escalation step number
            ack_url: ACK URL for responders
            policy_id: Escalation policy ID
        """
        steps = (
            await session.scalars(
                select(EscalationStep)
                .options(selectinload(EscalationStep.target))
                .where(EscalationStep.policy_id == policy_id)
                .where(EscalationStep.step_no == step_no)
            )
        ).all()

        message = format_alarm_message(
            alarm_id=str(alarm.id),
            person=str(enriched["person_name"]),
            room=str(enriched["room_label"]),
            site=str(enriched.get("site_name")) if enriched.get("site_name") else None,
            created_at=alarm.created_at,
            ack_url=ack_url,
            step_no=step_no,
        )

        for step in steps:
            target = step.target
            if not target.enabled:
                continue

            try:
                if target.channel == "sms":
                    await self._sendxms.send_sms(target.address, message)
                elif target.channel == "signal":
                    await self._signal.send_group_message(message, group_id=target.address)
                else:
                    logger.warning(
                        "unknown_channel",
                        extra={"channel": target.channel, "target_id": target.id},
                    )
                    continue

                await log_notification(
                    session,
                    alarm_id=alarm.id,
                    channel=target.channel,
                    target_id=target.id,
                    payload={"step_no": step_no},
                    result="ok",
                )
            except Exception as e:
                logger.exception(
                    "notification_failed",
                    extra={
                        "channel": target.channel,
                        "target_id": target.id,
                        "step_no": step_no,
                        "error": str(e),
                    },
                )
                await log_notification(
                    session,
                    alarm_id=alarm.id,
                    channel=target.channel,
                    target_id=target.id,
                    payload={"step_no": step_no},
                    result="error",
                    error=str(e),
                )

    async def get_escalation_schedule(
        self,
        session: AsyncSession,
        policy_id: str = "default",
    ) -> list[tuple[int, int]]:
        """Get escalation steps that need to be scheduled.

        Args:
            session: Database session
            policy_id: Escalation policy ID

        Returns:
            List of (step_no, after_seconds) tuples
        """
        rows = (
            await session.execute(
                select(EscalationStep.step_no, EscalationStep.after_seconds)
                .where(EscalationStep.policy_id == policy_id)
                .where(EscalationStep.step_no > 0)
                .distinct()
            )
        ).all()
        return [(row[0], row[1]) for row in rows]
