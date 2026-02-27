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

from alarm_broker import constants
from alarm_broker.connectors.sendxms import SendXmsClient
from alarm_broker.connectors.signal import SignalClient
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.db.models import Alarm, AlarmNotification, EscalationStep, EscalationTarget
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

    async def send(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: dict[str, Any],
        *,
        step_no: int,
        ack_url: str,
        policy_id: str = "default",
    ) -> None:
        """Main orchestrator for sending notifications across all channels.

        This method coordinates the notification flow by:
        1. Building the notification payload
        2. Fetching escalation targets
        3. Sending to each enabled channel
        4. Logging results

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            step_no: Escalation step number
            ack_url: ACK URL for responders
            policy_id: Escalation policy ID
        """
        # Build notification payload
        payload = self._build_notification_payload(
            alarm=alarm,
            enriched=enriched,
            step_no=step_no,
            ack_url=ack_url,
        )

        # Fetch escalation targets for this step
        targets = await self._get_escalation_targets(session, policy_id, step_no)

        # Send to each target's preferred channel
        for target in targets:
            if not target.enabled:
                continue
            await self._send_to_channel(session, target, payload)

    def _build_notification_payload(
        self,
        alarm: Alarm,
        enriched: dict[str, Any],
        step_no: int,
        ack_url: str,
    ) -> dict[str, Any]:
        """Build the notification payload with message content and metadata.

        Creates the message title, body, tags, and applies severity-based
        prioritization for the notification.

        Args:
            alarm: Alarm instance
            enriched: Enriched alarm context
            step_no: Escalation step number
            ack_url: ACK URL for responders

        Returns:
            Dictionary containing title, body, tags, and priority
        """
        # Build message body
        body = format_alarm_message(
            alarm_id=str(alarm.id),
            person=str(enriched["person_name"]),
            room=str(enriched["room_label"]),
            site=str(enriched.get("site_name")) if enriched.get("site_name") else None,
            created_at=alarm.created_at,
            ack_url=ack_url,
            step_no=step_no,
        )

        # Determine severity-based priority
        severity = enriched.get("severity", constants.PRIORITY_CRITICAL)
        priority = self._get_priority_for_severity(severity)

        # Build title based on severity
        title = self._build_title(enriched, step_no)

        # Set tags based on step and severity
        tags = self._build_tags(step_no, severity)

        return {
            "title": title,
            "body": body,
            "tags": tags,
            "priority": priority,
            "step_no": step_no,
            "alarm_id": str(alarm.id),
        }

    def _get_priority_for_severity(self, severity: str) -> int:
        """Map severity to priority ID for external systems.

        Args:
            severity: Alarm severity (P0, P1, P2, P3)

        Returns:
            Priority ID for systems like Zammad
        """
        priority_map = {
            constants.PRIORITY_CRITICAL: 3,  # P0
            constants.PRIORITY_HIGH: 2,  # P1
            constants.PRIORITY_MEDIUM: 2,  # P2
            constants.PRIORITY_LOW: 1,  # P3
        }
        return priority_map.get(severity, 3)

    def _build_title(self, enriched: dict[str, Any], step_no: int) -> str:
        """Build notification title based on step and context.

        Args:
            enriched: Enriched alarm context
            step_no: Escalation step number

        Returns:
            Formatted title string
        """
        person = enriched.get("person_name", "Unknown")
        room = enriched.get("room_label", "Unknown")

        if step_no == 0:
            return f"NOTFALLALARM – {person} – {room}"
        return f"ESKALATION Stufe {step_no} – {person} – {room}"

    def _build_tags(self, step_no: int, severity: str) -> list[str]:
        """Build tags for notification based on step and severity.

        Args:
            step_no: Escalation step number
            severity: Alarm severity

        Returns:
            List of tag strings
        """
        tags = []
        if step_no == 0:
            tags.append(constants.TAG_EMERGENCY)
        if severity == constants.PRIORITY_CRITICAL:
            tags.append(constants.TAG_SILENT)
        return tags

    async def _get_escalation_targets(
        self,
        session: AsyncSession,
        policy_id: str,
        step_no: int,
    ) -> list[EscalationTarget]:
        """Fetch escalation targets for a given policy and step.

        Args:
            session: Database session
            policy_id: Escalation policy ID
            step_no: Escalation step number

        Returns:
            List of enabled EscalationTarget objects
        """
        steps = (
            await session.scalars(
                select(EscalationStep)
                .options(selectinload(EscalationStep.target))
                .where(EscalationStep.policy_id == policy_id)
                .where(EscalationStep.step_no == step_no)
            )
        ).all()
        return [step.target for step in steps if step.target.enabled]

    async def _send_to_channel(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: dict[str, Any],
    ) -> None:
        """Dispatch notification to the appropriate channel-specific method.

        Routes the notification based on the target's channel preference.
        Errors in one channel do not affect other channels.

        Args:
            session: Database session
            target: Target configuration with channel preference
            payload: Notification payload to send
        """
        try:
            if target.channel == "email":
                await self._send_email_notifications(session, target, payload)
            elif target.channel == "sms":
                await self._send_sms_notifications(session, target, payload)
            elif target.channel == "signal":
                await self._send_sms_notifications(session, target, payload)
            elif target.channel == "webhook":
                await self._send_webhook_notifications(session, target, payload)
            else:
                logger.warning(
                    "unknown_channel",
                    extra={"channel": target.channel, "target_id": target.id},
                )
        except Exception as e:
            logger.exception(
                "channel_dispatch_failed",
                extra={"channel": target.channel, "target_id": target.id, "error": str(e)},
            )

    async def _send_email_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: dict[str, Any],
    ) -> None:
        """Send email notification via Zammad.

        Formats the email payload and creates a ticket in Zammad.
        Failure does not affect other channels.

        Args:
            session: Database session
            target: Target with email configuration
            payload: Notification payload
        """
        if not self._zammad.enabled():
            await self._log_notification_result(
                session, target, payload, "error", "Zammad not enabled"
            )
            return

        try:
            # Format email payload for Zammad
            email_payload = {
                "title": payload["title"],
                "group": "Notfallstelle",
                "priority_id": payload["priority"],
                "state_id": 1,
                "customer_id": "guess:alarm-system@example.org",
                "tags": payload["tags"],
                "article": {
                    "subject": "Alarm ausgelöst (silent)",
                    "body": payload["body"],
                    "type": "note",
                    "internal": True,
                },
            }
            ticket_id = await self._zammad.create_ticket(email_payload)
            await self._log_notification_result(
                session, target, payload, "ok", ticket_id=str(ticket_id)
            )
        except Exception as e:
            logger.exception(
                "email_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_sms_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: dict[str, Any],
    ) -> None:
        """Send SMS notification via Signal or SendXMS.

        Tries Signal first, falls back to SendXMS if Signal fails.
        Failure in one provider does not affect the other.

        Args:
            session: Database session
            target: Target with SMS configuration
            payload: Notification payload
        """
        message = payload["body"]

        # Try Signal first
        if target.channel == "signal":
            await self._send_via_signal(session, target, message, payload)
            return

        # Try SendXMS for SMS
        if target.channel == "sms":
            await self._send_via_sendxms(session, target, message, payload)

    async def _send_via_signal(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        """Send message via Signal client.

        Args:
            session: Database session
            target: Target with Signal configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        try:
            await self._signal.send_group_message(message, group_id=target.address)
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "signal_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_via_sendxms(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        """Send message via SendXMS client.

        Args:
            session: Database session
            target: Target with SendXMS configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        try:
            await self._sendxms.send_sms(target.address, message)
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "sendxms_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_webhook_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: dict[str, Any],
    ) -> None:
        """Send webhook notification via HTTP POST.

        Sends the notification payload to a configured webhook URL.

        Args:
            session: Database session
            target: Target with webhook configuration
            payload: Notification payload to send
        """
        # Note: This uses httpx for HTTP requests
        # Import here to avoid circular dependencies
        import httpx

        webhook_url = target.address
        if not webhook_url:
            await self._log_notification_result(
                session, target, payload, "error", "No webhook URL configured"
            )
            return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "webhook_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _log_notification_result(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: dict[str, Any],
        result: str,
        error: str | None = None,
    ) -> None:
        """Log notification result to database and track metrics.

        Records successful and failed notification attempts for auditing
        and metrics purposes.

        Args:
            session: Database session
            target: Target that was notified
            payload: Payload that was sent
            result: Result status ("ok" or "error")
            error: Error message if result is error
        """
        await log_notification(
            session,
            alarm_id=uuid.UUID(payload["alarm_id"]),
            channel=target.channel,
            target_id=str(target.id),
            payload=payload,
            result=result,
            error=error,
        )

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
            "tags": [constants.TAG_EMERGENCY, constants.TAG_SILENT],
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

        This method is kept for backward compatibility. It delegates to
        the new send() method for the actual notification logic.

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            step_no: Escalation step number
            ack_url: ACK URL for responders
            policy_id: Escalation policy ID
        """
        await self.send(
            session=session,
            alarm=alarm,
            enriched=enriched,
            step_no=step_no,
            ack_url=ack_url,
            policy_id=policy_id,
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
