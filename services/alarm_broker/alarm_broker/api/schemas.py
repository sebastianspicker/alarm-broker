from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from alarm_broker.db.models import AlarmStatus


class TriggerResponse(BaseModel):
    ok: bool = True
    alarm_id: uuid.UUID
    status: AlarmStatus


class AlarmOut(BaseModel):
    id: uuid.UUID
    status: AlarmStatus
    source: str
    event: str
    created_at: datetime
    person_id: str | None
    room_id: str | None
    site_id: str | None
    device_id: str | None
    severity: str
    silent: bool
    zammad_ticket_id: int | None
    ack_token: str | None
    acked_at: datetime | None
    acked_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    meta: dict[str, Any] = Field(default_factory=dict)


class AckIn(BaseModel):
    acked_by: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class TransitionIn(BaseModel):
    actor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class DeviceUpsertIn(BaseModel):
    id: str | None = None
    vendor: str = "yealink"
    model_family: str = "T5"
    mac: str | None = None
    account_ext: str | None = None
    device_token: str
    person_id: str | None = None
    room_id: str | None = None


class TargetIn(BaseModel):
    id: str
    label: str
    channel: str
    address: str
    enabled: bool = True


class StepIn(BaseModel):
    step_no: int
    after_seconds: int
    target_ids: list[str]


class EscalationPolicyIn(BaseModel):
    policy_id: str = "default"
    name: str = "Default"
    targets: list[TargetIn] = Field(default_factory=list)
    steps: list[StepIn] = Field(default_factory=list)


class AlarmNoteIn(BaseModel):
    """Input schema for creating an alarm note."""

    note: str = Field(..., min_length=1, max_length=5000)
    created_by: str | None = Field(default=None, max_length=120)


class AlarmNoteOut(BaseModel):
    """Output schema for alarm notes."""

    id: uuid.UUID
    alarm_id: uuid.UUID
    created_at: datetime
    created_by: str | None
    note: str
    note_type: str

    model_config = {"from_attributes": True}
