from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import (
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)
from alarm_broker.settings import Settings

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return bool(value)


def _expand_env(value: Any, settings: Settings) -> Any:
    if isinstance(value, str):
        m = _ENV_PATTERN.match(value.strip())
        if not m:
            return value
        key = m.group(1)
        env_val = os.getenv(key)
        if env_val is None:
            # allow referencing Settings fields (upper snake -> lower snake)
            settings_key = key.lower()
            env_val = str(getattr(settings, settings_key, "")) or None
        if env_val is None:
            return None
        lowered = env_val.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        if env_val.isdigit():
            return int(env_val)
        return env_val
    if isinstance(value, list):
        return [_expand_env(v, settings) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v, settings) for k, v in value.items()}
    return value


async def apply_seed(session: AsyncSession, raw: dict[str, Any], settings: Settings) -> None:
    data = _expand_env(raw or {}, settings)

    for s in data.get("sites", []) or []:
        obj = await session.get(Site, s["id"])
        if not obj:
            session.add(Site(id=s["id"], name=s["name"]))
        else:
            obj.name = s["name"]

    for r in data.get("rooms", []) or []:
        obj = await session.get(Room, r["id"])
        if not obj:
            session.add(
                Room(
                    id=r["id"],
                    site_id=r["site_id"],
                    label=r["label"],
                    floor=r.get("floor"),
                    notes=r.get("notes"),
                )
            )
        else:
            obj.site_id = r["site_id"]
            obj.label = r["label"]
            obj.floor = r.get("floor")
            obj.notes = r.get("notes")

    for p in data.get("persons", []) or []:
        obj = await session.get(Person, p["id"])
        if not obj:
            session.add(
                Person(
                    id=p["id"],
                    display_name=p["display_name"],
                    role=p.get("role"),
                    phone_mobile=p.get("phone_mobile"),
                    phone_ext=p.get("phone_ext"),
                    active=_coerce_bool(p.get("active", True)),
                )
            )
        else:
            obj.display_name = p["display_name"]
            obj.role = p.get("role")
            obj.phone_mobile = p.get("phone_mobile")
            obj.phone_ext = p.get("phone_ext")
            obj.active = _coerce_bool(p.get("active", True))

    for d in data.get("devices", []) or []:
        obj = await session.scalar(select(Device).where(Device.device_token == d["device_token"]))
        if not obj:
            session.add(
                Device(
                    id=d["id"],
                    vendor=d.get("vendor", "yealink"),
                    model_family=d.get("model_family", "T5"),
                    mac=d.get("mac"),
                    account_ext=d.get("account_ext"),
                    device_token=d["device_token"],
                    person_id=d.get("person_id"),
                    room_id=d.get("room_id"),
                )
            )
        else:
            obj.id = d.get("id", obj.id)
            obj.vendor = d.get("vendor", obj.vendor)
            obj.model_family = d.get("model_family", obj.model_family)
            obj.mac = d.get("mac")
            obj.account_ext = d.get("account_ext")
            obj.person_id = d.get("person_id")
            obj.room_id = d.get("room_id")

    policy = data.get("escalation_policy")
    if policy:
        obj = await session.get(EscalationPolicy, policy.get("id", "default"))
        if not obj:
            session.add(
                EscalationPolicy(id=policy.get("id", "default"), name=policy.get("name", "Default"))
            )
        else:
            obj.name = policy.get("name", obj.name)

    for t in data.get("escalation_targets", []) or []:
        obj = await session.get(EscalationTarget, t["id"])
        if not obj:
            session.add(
                EscalationTarget(
                    id=t["id"],
                    label=t["label"],
                    channel=t["channel"],
                    address=t["address"],
                    enabled=_coerce_bool(t.get("enabled", True)),
                )
            )
        else:
            obj.label = t["label"]
            obj.channel = t["channel"]
            obj.address = t["address"]
            obj.enabled = _coerce_bool(t.get("enabled", True))

    # Replace steps for policies included in the seed
    steps = data.get("escalation_steps", []) or []
    if steps:
        policy_ids = sorted({s["policy_id"] for s in steps})
        for pid in policy_ids:
            existing = await session.scalars(
                select(EscalationStep).where(EscalationStep.policy_id == pid)
            )
            for row in existing:
                await session.delete(row)

        for s in steps:
            for target_id in s.get("target_ids") or []:
                session.add(
                    EscalationStep(
                        policy_id=s["policy_id"],
                        step_no=int(s["step_no"]),
                        after_seconds=int(s["after_seconds"]),
                        target_id=target_id,
                    )
                )

    await session.commit()
