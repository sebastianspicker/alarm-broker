"""Trigger service for handling alarm triggers.

This service encapsulates the logic for processing alarm triggers,
including idempotency, rate limiting, device validation, and alarm creation.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker import constants
from alarm_broker.core.idempotency import bucket_10s, idempotency_key
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key
from alarm_broker.db.models import Alarm, AlarmStatus, Device, Room
from alarm_broker.services.event_publisher import EventPublisher
from alarm_broker.settings import Settings

logger = logging.getLogger("alarm_broker")


def _hash_token_for_logging(token: str) -> str:
    """Create a safe hash of the token for logging purposes."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class TriggerResult:
    """Result of a trigger operation.

    Attributes:
        success: Whether the trigger was successful
        alarm_id: ID of the alarm (new or existing)
        status: Status of the alarm
        is_duplicate: Whether this was a duplicate/idempotent request
        error_code: HTTP error code if failed
        error_message: Error message if failed
    """

    def __init__(
        self,
        *,
        success: bool = True,
        alarm_id: uuid.UUID | None = None,
        status: AlarmStatus | None = None,
        is_duplicate: bool = False,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        self.success = success
        self.alarm_id = alarm_id
        self.status = status
        self.is_duplicate = is_duplicate
        self.error_code = error_code
        self.error_message = error_message

    @classmethod
    def ok(
        cls, alarm_id: uuid.UUID, status: AlarmStatus, is_duplicate: bool = False
    ) -> TriggerResult:
        """Create a successful result."""
        return cls(success=True, alarm_id=alarm_id, status=status, is_duplicate=is_duplicate)

    @classmethod
    def error(cls, code: int, message: str) -> TriggerResult:
        """Create an error result."""
        return cls(success=False, error_code=code, error_message=message)


class TriggerService:
    """Service for handling alarm triggers.

    This service encapsulates all trigger logic including:
    - Idempotency checking
    - Rate limiting
    - Device validation
    - Alarm creation
    """

    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        settings: Settings,
        idempotency_bucket: int | None = None,
        rate_limit_bucket: int | None = None,
    ) -> None:
        """Initialize the trigger service.

        Args:
            session: Database session
            redis: Redis connection for idempotency/rate limiting
            settings: Application settings
            idempotency_bucket: Optional pre-computed idempotency bucket
            rate_limit_bucket: Optional pre-computed rate limit bucket
        """
        self._session = session
        self._redis = redis
        self._settings = settings
        self._idempotency_bucket = (
            idempotency_bucket if idempotency_bucket is not None else bucket_10s()
        )
        self._rate_limit_bucket = (
            rate_limit_bucket if rate_limit_bucket is not None else minute_bucket()
        )
        self._event_publisher = EventPublisher(redis)

    def _get_idempotency_key(self, token: str) -> str:
        """Get the Redis key for idempotency checking.

        Args:
            token: Device token

        Returns:
            Redis key string
        """
        idem = idempotency_key(token, self._idempotency_bucket)
        return f"idemp:{idem}"

    def _get_rate_limit_key(self, token: str) -> str:
        """Get the Redis key for rate limiting.

        Args:
            token: Device token

        Returns:
            Redis key string
        """
        return rate_limit_key(token, self._rate_limit_bucket)

    async def check_idempotency(self, token: str) -> tuple[bool, uuid.UUID | None]:
        """Check if this request is idempotent (duplicate).

        Args:
            token: Device token

        Returns:
            Tuple of (is_duplicate, existing_alarm_id)
        """
        idem_key = self._get_idempotency_key(token)

        existing_alarm_id = await self._redis.get(idem_key)
        if not existing_alarm_id:
            return False, None

        try:
            existing_uuid = uuid.UUID(existing_alarm_id)
        except ValueError:
            # Invalid UUID in Redis, clear it
            await self._redis.delete(idem_key)
            return False, None

        return True, existing_uuid

    async def reserve_alarm_id(self, token: str) -> uuid.UUID | None:
        """Reserve an alarm ID for idempotency.

        Args:
            token: Device token

        Returns:
            Reserved UUID or None if reservation failed
        """
        idem_key = self._get_idempotency_key(token)

        # Try up to 3 times to handle race conditions
        for _attempt in range(3):
            reserved_id = uuid.uuid4()
            ok = await self._redis.set(idem_key, str(reserved_id), ex=30, nx=True)
            if ok:
                return reserved_id
            # Check if there's an existing alarm ID we can use
            existing = await self._redis.get(idem_key)
            if existing:
                try:
                    return uuid.UUID(existing)
                except ValueError:
                    # Invalid UUID, delete and retry
                    await self._redis.delete(idem_key)
        return None

    async def clear_idempotency(self, token: str) -> None:
        """Clear idempotency key (on error).

        Args:
            token: Device token
        """
        idem_key = self._get_idempotency_key(token)
        await self._redis.delete(idem_key)

    async def check_rate_limit(self, token: str) -> bool:
        """Check if the request is within rate limits.

        Args:
            token: Device token

        Returns:
            True if within limits, False if exceeded
        """
        rl_key = self._get_rate_limit_key(token)
        rl_val = await self._redis.incr(rl_key)
        if rl_val == 1:
            await self._redis.expire(rl_key, 70)
        return rl_val <= self._settings.rate_limit_per_minute

    async def validate_device(self, token: str) -> tuple[Device | None, str | None]:
        """Validate device token and get device.

        Args:
            token: Device token

        Returns:
            Tuple of (device, error_message)
        """
        device = await self._session.scalar(select(Device).where(Device.device_token == token))
        if not device:
            return None, "Unknown token"
        if not device.person_id or not device.room_id:
            return None, "Device mapping incomplete"
        return device, None

    async def create_alarm(
        self,
        device: Device,
        alarm_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
        event: str | None = None,
    ) -> Alarm:
        """Create a new alarm.

        Args:
            device: Device that triggered the alarm
            alarm_id: Pre-reserved alarm ID
            client_ip: Client IP address
            user_agent: User agent string
            event: Event type (optional)

        Returns:
            Created alarm instance
        """
        room = await self._session.get(Room, device.room_id)
        site_id = room.site_id if room else None

        ack_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        device.last_seen_at = now

        idem = idempotency_key(device.device_token, self._idempotency_bucket)

        alarm = Alarm(
            id=alarm_id,
            status=AlarmStatus.TRIGGERED,
            source="yealink",
            event=event or "alarm.trigger",
            person_id=device.person_id,
            room_id=device.room_id,
            site_id=site_id,
            device_id=device.id,
            severity=constants.DEFAULT_SEVERITY,
            silent=True,
            ack_token=ack_token,
            meta={
                "received_at": now.isoformat(),
                "client_ip": client_ip,
                "user_agent": user_agent,
                "idempotency": {"bucket": self._idempotency_bucket, "key": idem},
            },
        )
        self._session.add(alarm)
        await self._session.commit()

        return alarm

    async def enqueue_alarm_created(self, alarm_id: uuid.UUID) -> bool:
        """Enqueue the alarm_created event.

        Args:
            alarm_id: ID of the created alarm

        Returns:
            True if enqueued successfully
        """
        try:
            await self._event_publisher.publish_alarm_created(alarm_id=str(alarm_id))
            return True
        except Exception:
            logger.exception("enqueue_alarm_created_failed", extra={"alarm_id": str(alarm_id)})
            return False

    def _validate_trigger(
        self,
        token: str,
        severity: str | None = None,
    ) -> tuple[bool, str | None]:
        """Validate trigger data.

        Checks required fields and severity validity.

        Args:
            token: Device token
            severity: Optional severity level

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not token or not token.strip():
            return False, "Token is required"
        if severity is not None and severity not in constants.PRIORITY_ALL:
            return False, f"Invalid severity: {severity}"
        return True, None

    async def _check_idempotency(
        self,
        token: str,
    ) -> tuple[bool, uuid.UUID | None, Alarm | None]:
        """Check idempotency and return existing alarm if found.

        Args:
            token: Device token

        Returns:
            Tuple of (is_duplicate, alarm_id, existing_alarm)
        """
        is_duplicate, existing_id = await self.check_idempotency(token)
        if is_duplicate and existing_id:
            existing_alarm = await self._session.get(Alarm, existing_id)
            if existing_alarm:
                logger.info(
                    "trigger_idempotent",
                    extra={
                        "alarm_id": str(existing_id),
                        "token_hash": _hash_token_for_logging(token),
                    },
                )
                return True, existing_id, existing_alarm
            # Invalid reference, clear and continue
            await self.clear_idempotency(token)
        return False, None, None

    async def _reserve_idempotency_key(self, token: str) -> uuid.UUID | None:
        """Reserve idempotency key for the request.

        Args:
            token: Device token

        Returns:
            Reserved UUID or None if reservation failed
        """
        alarm_id = await self.reserve_alarm_id(token)
        if not alarm_id:
            logger.error(
                "idempotency_reservation_failed",
                extra={"token_hash": _hash_token_for_logging(token)},
            )
        return alarm_id

    async def _check_rate_limit(self, token: str) -> bool:
        """Check if request is within rate limits.

        Args:
            token: Device token

        Returns:
            True if within limits, False if exceeded
        """
        return await self.check_rate_limit(token)

    async def _enrich_trigger_data(
        self,
        token: str,
    ) -> tuple[Device | None, str | None]:
        """Enrich trigger data by validating and fetching device info.

        Args:
            token: Device token

        Returns:
            Tuple of (device, error_message)
        """
        return await self.validate_device(token)

    async def _evaluate_policies(
        self,
        device: Device,
    ) -> dict[str, Any]:
        """Evaluate escalation policies for the device.

        Determines notification targets based on escalation policies.

        Args:
            device: Validated device

        Returns:
            Dictionary with policy evaluation results
        """
        # TODO: Implement policy evaluation logic
        # For now, return default configuration
        return {
            "use_default_severity": True,
            "severity": constants.DEFAULT_SEVERITY,
            "notification_targets": [],
        }

    async def _create_alarm(
        self,
        device: Device,
        alarm_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
        event: str | None = None,
    ) -> Alarm:
        """Create alarm object instance.

        Args:
            device: Validated device
            alarm_id: Pre-reserved alarm ID
            client_ip: Client IP address
            user_agent: User agent string
            event: Event type (optional)

        Returns:
            Created alarm instance
        """
        return await self.create_alarm(
            device=device,
            alarm_id=alarm_id,
            client_ip=client_ip,
            user_agent=user_agent,
            event=event,
        )

    async def _send_notifications(self, alarm: Alarm) -> bool:
        """Send notifications for the created alarm.

        Calls NotificationService to send notifications to appropriate targets.

        Args:
            alarm: Created alarm instance

        Returns:
            True if notifications sent successfully
        """
        # Enqueue notification task
        notification_ok = await self.enqueue_alarm_created(alarm.id)

        # Enqueue state changed event via EventPublisher
        await self._event_publisher.publish_alarm_state_changed(
            alarm_id=str(alarm.id),
            old_state="none",
            new_state=alarm.status.value,
        )

        return notification_ok

    async def process_trigger(
        self,
        token: str,
        client_ip: str,
        user_agent: str,
        event: str | None = None,
    ) -> TriggerResult:
        """Process an alarm trigger request (orchestrator).

        Delegates to smaller methods: validate, idempotency check,
        rate limit check, enrich data, evaluate policies,
        create alarm, send notifications.
        """
        # Step 1: Validate trigger data
        is_valid, validation_error = self._validate_trigger(token)
        if not is_valid:
            return TriggerResult.error(400, validation_error)

        # Step 2: Check idempotency - return existing if duplicate
        is_duplicate, _, existing_alarm = await self._check_idempotency(token)
        if is_duplicate and existing_alarm:
            return TriggerResult.ok(existing_alarm.id, existing_alarm.status, is_duplicate=True)

        # Step 3: Reserve idempotency key
        alarm_id = await self._reserve_idempotency_key(token)
        if not alarm_id:
            return TriggerResult.error(500, "Idempotency failure")

        # Step 4: Check rate limit
        if not await self._check_rate_limit(token):
            await self.clear_idempotency(token)
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "token_hash": _hash_token_for_logging(token),
                    "limit": self._settings.rate_limit_per_minute,
                },
            )
            return TriggerResult.error(429, "Rate limit exceeded")

        # Step 5: Enrich trigger data (validate device)
        device, device_error = await self._enrich_trigger_data(token)
        if device_error:
            await self.clear_idempotency(token)
            status_code = 404 if device_error == "Unknown token" else 409
            return TriggerResult.error(status_code, device_error)

        # Step 6-8: Evaluate policies, create alarm, send notifications
        await self._evaluate_policies(device)
        alarm = await self._create_alarm(device, alarm_id, client_ip, user_agent, event)
        await self._send_notifications(alarm)

        logger.info(
            "alarm_triggered",
            extra={
                "alarm_id": str(alarm.id),
                "device_id": device.id,
                "person_id": device.person_id,
                "room_id": device.room_id,
            },
        )
        return TriggerResult.ok(alarm.id, alarm.status)
