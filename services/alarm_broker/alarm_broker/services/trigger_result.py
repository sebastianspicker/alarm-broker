"""Value object returned by the alarm trigger service."""

from __future__ import annotations

import uuid

from alarm_broker.db.models import AlarmStatus


class TriggerResult:
    """Outcome of a trigger operation."""

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
        return cls(success=True, alarm_id=alarm_id, status=status, is_duplicate=is_duplicate)

    @classmethod
    def error(cls, code: int, message: str) -> TriggerResult:
        return cls(success=False, error_code=code, error_message=message)
