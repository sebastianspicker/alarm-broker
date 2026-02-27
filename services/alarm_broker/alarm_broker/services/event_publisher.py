"""Event Publisher - Zentralisiert das Event-Enqueuing."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from arq.connections import ArqRedis

from alarm_broker.constants import (
    EVENT_ALARM_ACKNOWLEDGED,
    EVENT_ALARM_CANCELLED,
    EVENT_ALARM_CREATED,
    EVENT_ALARM_RESOLVED,
    EVENT_ALARM_STATE_CHANGED,
)

if TYPE_CHECKING:
    from alarm_broker.db.models import Alarm


class EventPublisher:
    """Zentralisierter Event-Publisher für Alarm-Events.

    Diese Klasse abstrahiert das Event-Enqueuing an einer zentralen Stelle
    und bietet eine einfache API für das Publishing von Alarm-Events.

    Usage:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_created(alarm_id=123)
        await publisher.publish_alarm_acknowledged(alarm_id=123, acknowledged_by="user@example.com")
    """

    # Job name for processing alarm events
    JOB_NAME = "process_alarm_event"

    def __init__(self, redis: ArqRedis):
        """Initialize the EventPublisher.

        Args:
            redis: ArqRedis instance for enqueuing jobs
        """
        self._redis = redis

    async def publish_alarm_created(self, alarm_id: int | str, **kwargs: Any) -> None:
        """Publish ein alarm.created Event.

        Args:
            alarm_id: ID des Alarms
            **kwargs: Zusätzliche Payload-Felder
        """
        await self._publish(event_type=EVENT_ALARM_CREATED, alarm_id=alarm_id, **kwargs)

    async def publish_alarm_acknowledged(
        self, alarm_id: int | str, acknowledged_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish ein alarm.acknowledged Event.

        Args:
            alarm_id: ID des Alarms
            acknowledged_by: Wer den Alarm bestätigt hat
            note: Optionale Notiz
            **kwargs: Zusätzliche Payload-Felder
        """
        await self._publish(
            event_type=EVENT_ALARM_ACKNOWLEDGED,
            alarm_id=alarm_id,
            acknowledged_by=acknowledged_by,
            note=note,
            **kwargs,
        )

    async def publish_alarm_resolved(
        self, alarm_id: int | str, resolved_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish ein alarm.resolved Event.

        Args:
            alarm_id: ID des Alarms
            resolved_by: Wer den Alarm gelöst hat
            note: Optionale Notiz
            **kwargs: Zusätzliche Payload-Felder
        """
        await self._publish(
            event_type=EVENT_ALARM_RESOLVED,
            alarm_id=alarm_id,
            resolved_by=resolved_by,
            note=note,
            **kwargs,
        )

    async def publish_alarm_cancelled(
        self, alarm_id: int | str, cancelled_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish ein alarm.cancelled Event.

        Args:
            alarm_id: ID des Alarms
            cancelled_by: Wer den Alarm storniert hat
            note: Optionale Notiz
            **kwargs: Zusätzliche Payload-Felder
        """
        await self._publish(
            event_type=EVENT_ALARM_CANCELLED,
            alarm_id=alarm_id,
            cancelled_by=cancelled_by,
            note=note,
            **kwargs,
        )

    async def publish_alarm_state_changed(
        self, alarm_id: int | str, old_state: str, new_state: str, **kwargs: Any
    ) -> None:
        """Publish ein alarm.state_changed Event.

        Args:
            alarm_id: ID des Alarms
            old_state: Vorheriger Status
            new_state: Neuer Status
            **kwargs: Zusätzliche Payload-Felder
        """
        await self._publish(
            event_type=EVENT_ALARM_STATE_CHANGED,
            alarm_id=alarm_id,
            old_state=old_state,
            new_state=new_state,
            **kwargs,
        )

    async def _publish(self, event_type: str, alarm_id: int | str, **kwargs: Any) -> None:
        """Interne Methode zum tatsächlichen Enqueuen.

        Args:
            event_type: Typ des Events
            alarm_id: ID des Alarms
            **kwargs: Zusätzliche Payload-Felder
        """
        payload = {
            "event_type": event_type,
            "alarm_id": alarm_id,
            "timestamp": datetime.now(UTC).isoformat(),
            **kwargs,
        }
        await self._redis.enqueue_job(self.JOB_NAME, payload)

    @classmethod
    def from_alarm(cls, redis: ArqRedis, alarm: "Alarm") -> "EventPublisher":
        """Factory-Methode um einen Publisher mit Alarm-Context zu erstellen.

        Diese Methode erstellt einen EventPublisher und bindet optional
        zusätzliche Felder aus dem Alarm-Objekt.

        Args:
            redis: ArqRedis instance
            alarm: Alarm-Objekt

        Returns:
            EventPublisher Instanz (funktioniert wie eine normale Instanz)
        """
        # Return normal instance - alarm context can be passed via kwargs
        return cls(redis)
