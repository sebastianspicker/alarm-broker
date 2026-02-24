from __future__ import annotations

import json
from typing import Any

import yaml
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.seed import apply_seed
from alarm_broker.settings import Settings

_YAML_TYPES = {
    "application/x-yaml",
    "application/yaml",
    "application/yml",
    "text/yaml",
    "text/x-yaml",
}


def parse_seed_payload(content_type: str, raw: bytes) -> dict[str, Any]:
    if content_type in _YAML_TYPES:
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid YAML seed payload",
            ) from exc
    else:
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON seed payload",
            ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seed payload must be a JSON/YAML object",
        )

    return data


async def apply_seed_payload(
    session: AsyncSession,
    *,
    data: dict[str, Any],
    settings: Settings,
) -> None:
    try:
        await apply_seed(session, data, settings)
    except (KeyError, TypeError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid seed structure or values",
        ) from exc
