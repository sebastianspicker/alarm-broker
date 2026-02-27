"""Yealink alarm trigger routes.

This module provides the endpoint for Yealink phone alarm triggers.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_client_ip, get_redis, get_session
from alarm_broker.api.schemas import TriggerResponse
from alarm_broker.core.idempotency import bucket_10s
from alarm_broker.core.ip_allowlist import ip_allowed
from alarm_broker.core.rate_limit import minute_bucket
from alarm_broker.services.trigger_service import TriggerService
from alarm_broker.settings import Settings

router = APIRouter()
logger = logging.getLogger("alarm_broker")


@router.get("/v1/yealink/alarm", response_model=TriggerResponse)
async def yealink_alarm(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> TriggerResponse:
    """Handle Yealink alarm trigger.

    This endpoint receives alarm triggers from Yealink phones and:
    1. Validates the source IP
    2. Checks idempotency to prevent duplicates
    3. Enforces rate limits
    4. Validates the device token
    5. Creates the alarm
    6. Enqueues notification tasks

    Args:
        request: FastAPI request
        session: Database session
        settings: Application settings

    Returns:
        TriggerResponse with alarm_id and status

    Raises:
        HTTPException: On validation or processing errors
    """
    # Validate source IP (skip in simulation mode but log warning)
    client_ip = get_client_ip(request, settings)
    if not settings.simulation_enabled:
        if not ip_allowed(client_ip, settings.yelk_ip_allowlist):
            logger.warning(
                "ip_not_allowed",
                extra={"client_ip": client_ip, "path": request.url.path},
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="IP not allowed")
    else:
        # In simulation mode, still validate but log for debugging
        if settings.yelk_ip_allowlist and not ip_allowed(client_ip, settings.yelk_ip_allowlist):
            logger.warning(
                "ip_not_allowed_simulation",
                extra={"client_ip": client_ip, "path": request.url.path},
            )

    # Get device token
    token = request.query_params.get(settings.yelk_token_query_param)
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing token")

    # Get Redis connection
    redis = get_redis(request)

    # Create trigger service with current bucket values
    # In simulation mode, disable rate limiting
    rate_bucket = None if settings.simulation_enabled else minute_bucket()
    trigger = TriggerService(
        session,
        redis,
        settings,
        idempotency_bucket=bucket_10s(),
        rate_limit_bucket=rate_bucket,
    )
    result = await trigger.process_trigger(
        token=token,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        event=request.query_params.get("event"),
    )

    # Handle result
    if not result.success:
        raise HTTPException(
            status_code=result.error_code or 500,
            detail=result.error_message or "Unknown error",
        )

    # Store alarm_id in request state for logging
    request.state.alarm_id = str(result.alarm_id)

    return TriggerResponse(alarm_id=result.alarm_id, status=result.status)
