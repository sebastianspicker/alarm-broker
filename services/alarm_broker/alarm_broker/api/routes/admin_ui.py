"""Server-rendered, session-authenticated operator console."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker import __version__
from alarm_broker.api.admin_session import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    AdminSession,
    create_admin_session,
    destroy_admin_session,
    pop_flash,
    require_admin_session,
    set_flash,
    validate_admin_csrf,
)
from alarm_broker.api.deps import (
    get_app_settings,
    get_client_ip,
    get_redis,
    get_session,
    is_secure_request,
)
from alarm_broker.api.i18n import SUPPORTED_LOCALES, normalise_locale, translation_context
from alarm_broker.api.schemas import EscalationPolicyIn
from alarm_broker.api.templating import render_template
from alarm_broker.connectors.mock import get_mock_store
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key
from alarm_broker.db.models import (
    AdminAuditEvent,
    Alarm,
    AlarmNote,
    AlarmNotification,
    AlarmStatus,
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)
from alarm_broker.services.admin_audit import add_admin_audit_event
from alarm_broker.services.alarm_service import (
    acknowledge_alarm,
    get_alarm_or_404,
    transition_alarm,
)
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)
from alarm_broker.services.master_data_lifecycle import require_current_version
from alarm_broker.services.policy_service import apply_escalation_policy
from alarm_broker.services.seed_service import apply_seed_payload, parse_seed_payload
from alarm_broker.settings import Settings

router = APIRouter()
logger = logging.getLogger("alarm_broker")
_FAILED_LOGIN_LIMIT = 5
_FAILED_LOGIN_WINDOW_SECONDS = 60


def escape(value: str) -> str:
    """Compatibility helper retained for callers that import it."""
    return html.escape(value, quote=True)


def _failed_login_key(request: Request, settings: Settings) -> str:
    return rate_limit_key(
        f"admin-login:{get_client_ip(request, settings)}",
        minute_bucket(),
    )


def _requested_locale(request: Request, explicit: str | None) -> str:
    if explicit in SUPPORTED_LOCALES:
        return explicit
    persisted = request.cookies.get("ui_locale")
    if persisted in SUPPORTED_LOCALES:
        return persisted
    return normalise_locale(request.headers.get("accept-language"))


def _base_context(request: Request, locale: str, **values: Any) -> dict[str, Any]:
    return {
        **translation_context(locale),
        "current_path": request.url.path,
        "worklist_url": f"/admin?lang={locale}",
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        **values,
    }


def _html(
    request: Request,
    template: str,
    locale: str,
    *,
    status_code: int = 200,
    persist_locale: bool = False,
    **context: Any,
) -> HTMLResponse:
    response = HTMLResponse(
        render_template(template, **_base_context(request, locale, **context)),
        status_code=status_code,
    )
    if persist_locale:
        response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


def _login_error(locale: str, kind: str) -> str:
    messages = {
        "en": {
            "invalid": "The admin key is not valid.",
            "rate": "Too many failed attempts. Wait one minute and try again.",
            "config": "Administrator login is not configured.",
        },
        "de": {
            "invalid": "Der Admin-Schlüssel ist nicht gültig.",
            "rate": "Zu viele fehlgeschlagene Versuche. Bitte warten Sie eine Minute.",
            "config": "Die Administrator-Anmeldung ist nicht konfiguriert.",
        },
    }
    return messages[locale][kind]


async def _session_from_request(
    request: Request,
    settings: Settings,
    token: str | None,
    *,
    extend: bool,
) -> AdminSession:
    return await require_admin_session(get_redis(request), settings, token, extend=extend)


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(
    request: Request, lang: str | None = Query(default=None)
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    return _html(
        request,
        "admin_login.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        login_action=f"/admin/login?lang={locale}",
        error=None,
    )


@router.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    admin_key: str = Form(...),
    operator_name: str = Form(default="Admin", max_length=120),
    lang: str | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    locale = _requested_locale(request, lang)
    if not settings.admin_api_key:
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=500,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "config"),
        )

    redis = get_redis(request)
    failure_key = _failed_login_key(request, settings)
    attempts = await redis.get(failure_key)
    if attempts is not None and int(attempts) >= _FAILED_LOGIN_LIMIT:
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=429,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "rate"),
        )
    if not secrets.compare_digest(admin_key, settings.admin_api_key):
        count = await redis.incr(failure_key)
        if count == 1:
            await redis.expire(failure_key, _FAILED_LOGIN_WINDOW_SECONDS)
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=401,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "invalid"),
        )

    await redis.delete(failure_key)
    named = operator_name.strip() or "Admin"
    browser_session = await create_admin_session(redis, settings, named)
    response = RedirectResponse(f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        browser_session.token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


@router.post("/admin/logout")
async def admin_logout(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    browser_session = await _session_from_request(request, settings, admin_session, extend=False)
    validate_admin_csrf(browser_session, csrf_token)
    await destroy_admin_session(get_redis(request), admin_session)
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.post("/admin/session/extend")
async def admin_extend_session(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


def _alarm_query(
    status_filter: str | None,
    search: str | None,
    sort_by: str,
    order: str,
    cursor: uuid.UUID | None,
    limit: int,
):
    stmt = (
        select(Alarm, Person.display_name, Room.label)
        .outerjoin(Person, Person.id == Alarm.person_id)
        .outerjoin(Room, Room.id == Alarm.room_id)
        .where(Alarm.deleted_at.is_(None))
    )
    if status_filter in {item.value for item in AlarmStatus}:
        stmt = stmt.where(Alarm.status == AlarmStatus(status_filter))
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                cast(Alarm.id, String).ilike(pattern),
                Alarm.source.ilike(pattern),
                Alarm.event.ilike(pattern),
                Person.display_name.ilike(pattern),
                Room.label.ilike(pattern),
            )
        )
    if cursor is not None:
        stmt = stmt.where(Alarm.id < cursor if order == "desc" else Alarm.id > cursor)
    sort_columns = {
        "status": Alarm.status,
        "severity": Alarm.severity,
        "created_at": Alarm.created_at,
    }
    sort_column = sort_columns.get(sort_by, Alarm.created_at)
    ordering = sort_column.desc() if order == "desc" else sort_column.asc()
    id_ordering = Alarm.id.desc() if order == "desc" else Alarm.id.asc()
    return stmt.order_by(ordering, id_ordering).limit(limit + 1)


async def _counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Alarm.status, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.status)
        )
    ).all()
    counts = {item.value: 0 for item in AlarmStatus}
    counts.update({state.value: int(count) for state, count in rows})
    return counts


def _display_time(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    minutes = max(0, int((datetime.now(UTC) - aware).total_seconds() // 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def _worklist_row(
    alarm: Alarm, person: str | None, room: str | None, locale: str
) -> dict[str, Any]:
    return {
        "id": str(alarm.id),
        "short_id": str(alarm.id)[:8],
        "status": alarm.status.value,
        "created_at": _display_time(alarm.created_at),
        "created_at_iso": alarm.created_at.isoformat(),
        "person": person or alarm.person_id or "—",
        "room": room or alarm.room_id or "—",
        "source": alarm.source,
        "severity": alarm.severity,
        "detail_url": f"/admin/alarms/{alarm.id}?lang={locale}",
    }


async def _revision(session: AsyncSession) -> str:
    rows = (
        await session.execute(
            select(
                func.count(Alarm.id),
                func.max(Alarm.created_at),
                func.max(Alarm.acked_at),
                func.max(Alarm.resolved_at),
                func.max(Alarm.cancelled_at),
            ).where(Alarm.deleted_at.is_(None))
        )
    ).one()
    return uuid.uuid5(uuid.NAMESPACE_OID, "|".join(str(value or "") for value in rows)).hex


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=120),
    sort_by: str = Query(default="created_at", pattern="^(created_at|status|severity)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    cursor: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    statement = _alarm_query(status_filter, search, sort_by, order, cursor, limit)
    result = list((await session.execute(statement)).all())
    page_rows = result[:limit]
    next_cursor = str(page_rows[-1][0].id) if len(result) > limit and page_rows else None
    flash = await pop_flash(get_redis(request), browser_session)
    return _html(
        request,
        "admin_worklist.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        alarms=[_worklist_row(alarm, person, room, locale) for alarm, person, room in page_rows],
        counts=await _counts(session),
        statuses=[item.value for item in AlarmStatus],
        filters={"status": status_filter or "", "search": search or ""},
        poll_url=f"/admin/revision?lang={locale}",
        poll_interval=15,
        revision=await _revision(session),
        next_cursor=next_cursor,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
        flash=flash,
        simulation_enabled=settings.simulation_enabled,
    )


@router.get("/admin/revision")
async def admin_revision(
    request: Request,
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    await _session_from_request(request, settings, admin_session, extend=False)
    return JSONResponse({"revision": await _revision(session)})


async def _detail_context(session: AsyncSession, alarm: Alarm, locale: str) -> dict[str, Any]:
    person = await session.get(Person, alarm.person_id) if alarm.person_id else None
    room = await session.get(Room, alarm.room_id) if alarm.room_id else None
    notes = list(
        (
            await session.scalars(
                select(AlarmNote)
                .where(AlarmNote.alarm_id == alarm.id)
                .order_by(AlarmNote.created_at.asc())
            )
        ).all()
    )
    notifications = list(
        (
            await session.scalars(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm.id)
                .order_by(AlarmNotification.created_at.asc())
            )
        ).all()
    )
    events: list[dict[str, str]] = [
        {
            "at": alarm.created_at.isoformat(timespec="minutes"),
            "at_iso": alarm.created_at.isoformat(),
            "description": "Alarm created" if locale == "en" else "Alarm erstellt",
        }
    ]
    events.extend(
        {
            "at": note.created_at.isoformat(timespec="minutes"),
            "at_iso": note.created_at.isoformat(),
            "description": f"{note.created_by or 'System'}: {note.note}",
        }
        for note in notes
    )
    events.extend(
        {
            "at": item.created_at.isoformat(timespec="minutes"),
            "at_iso": item.created_at.isoformat(),
            "description": f"{item.channel}: {item.result or 'pending'}",
        }
        for item in notifications
    )
    events.sort(key=lambda item: item["at_iso"])
    return {
        "alarm": {
            "id": str(alarm.id),
            "short_id": str(alarm.id)[:8],
            "status": alarm.status.value,
            "created_at": alarm.created_at.isoformat(timespec="minutes"),
            "person": person.display_name if person else alarm.person_id or "—",
            "room": room.label if room else alarm.room_id or "—",
            "source": alarm.source,
            "severity": alarm.severity,
            "can_ack": alarm.status == AlarmStatus.TRIGGERED,
            "can_close": alarm.status in {AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED},
        },
        "events": events,
    }


@router.get("/admin/alarms/{alarm_id}", response_class=HTMLResponse)
async def admin_alarm_detail(
    alarm_id: uuid.UUID,
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    alarm = await get_alarm_or_404(session, alarm_id)
    detail = await _detail_context(session, alarm, locale)
    return _html(
        request,
        "admin_detail.html",
        locale,
        **detail,
        ack_action=f"/admin/alarms/{alarm_id}/ack?lang={locale}",
        resolve_action=f"/admin/alarms/{alarm_id}/resolve?lang={locale}",
        cancel_action=f"/admin/alarms/{alarm_id}/cancel?lang={locale}",
        delete_action=f"/admin/alarms/{alarm_id}/delete?lang={locale}",
        note_action=f"/admin/alarms/{alarm_id}/notes?lang={locale}",
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


async def _enqueue_state(
    request: Request,
    alarm: Alarm,
    *,
    ack: bool,
    actor: str,
    note: str | None,
) -> bool:
    redis = get_redis(request)
    success = True
    if ack:
        ack_result = await enqueue_alarm_acked_event(
            redis, alarm_id=alarm.id, acked_by=actor, note=note, logger=logger
        )
        success = ack_result.success
    state_result = await enqueue_alarm_state_changed_event(
        redis, alarm_id=alarm.id, state=alarm.status.value, logger=logger
    )
    return success and state_result.success


async def _action_session(
    request: Request,
    settings: Settings,
    token: str | None,
    csrf_token: str | None,
) -> AdminSession:
    browser_session = await _session_from_request(request, settings, token, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    return browser_session


@router.post("/admin/alarms/{alarm_id}/ack")
async def admin_ack_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await acknowledge_alarm(
        session, alarm, acked_by=browser_session.operator_name, note=note
    )
    delivery_ok = (
        await _enqueue_state(
            request, alarm, ack=True, actor=browser_session.operator_name, note=note
        )
        if changed
        else True
    )
    await set_flash(
        get_redis(request),
        browser_session,
        "success" if delivery_ok else "warning",
        "alarm_acknowledged" if delivery_ok else "alarm_acknowledged_delivery_pending",
    )
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


async def _transition_from_form(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None,
    note: str | None,
    target: AlarmStatus,
    admin_session: str | None,
    session: AsyncSession,
    settings: Settings,
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    if target == AlarmStatus.CANCELLED and not (note or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await transition_alarm(
        session,
        alarm,
        target_status=target,
        actor=browser_session.operator_name,
        note=(note or "").strip() or None,
    )
    delivery_ok = (
        await _enqueue_state(
            request, alarm, ack=False, actor=browser_session.operator_name, note=note
        )
        if changed
        else True
    )
    await set_flash(
        get_redis(request), browser_session, "success" if delivery_ok else "warning", target.value
    )
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


@router.post("/admin/alarms/{alarm_id}/resolve")
async def admin_resolve_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return await _transition_from_form(
        alarm_id, request, csrf_token, note, AlarmStatus.RESOLVED, admin_session, session, settings
    )


@router.post("/admin/alarms/{alarm_id}/cancel")
async def admin_cancel_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    reason: str = Form(..., min_length=1, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return await _transition_from_form(
        alarm_id,
        request,
        csrf_token,
        reason,
        AlarmStatus.CANCELLED,
        admin_session,
        session,
        settings,
    )


@router.post("/admin/alarms/{alarm_id}/notes")
async def admin_add_note(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str = Form(..., min_length=1, max_length=5000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    session.add(
        AlarmNote(
            alarm_id=alarm.id,
            note=note.strip(),
            created_by=browser_session.operator_name,
            note_type="manual",
        )
    )
    await session.commit()
    await set_flash(get_redis(request), browser_session, "success", "note_added")
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


@router.post("/admin/alarms/{alarm_id}/delete")
async def admin_delete_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    reason: str = Form(..., min_length=1, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    alarm.deleted_at = datetime.now(UTC)
    alarm.deleted_by = browser_session.operator_name
    session.add(
        AlarmNote(
            alarm_id=alarm.id,
            note=reason.strip(),
            created_by=browser_session.operator_name,
            note_type="delete",
        )
    )
    await session.commit()
    await set_flash(get_redis(request), browser_session, "success", "alarm_deleted")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/alarms/bulk")
async def admin_bulk_action(
    request: Request,
    action: str = Form(..., pattern="^(ack|resolve|cancel)$"),
    csrf_token: str | None = Form(default=None),
    reason: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    form = await request.form()
    raw_ids = form.getlist("alarm_id")
    if not raw_ids:
        raise HTTPException(status_code=422, detail="selection_required")
    if action == "cancel" and not (reason or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")
    changed = unchanged = missing = 0
    for raw_id in raw_ids[:500]:
        try:
            alarm_id = uuid.UUID(str(raw_id))
        except ValueError:
            missing += 1
            continue
        alarm = await session.get(Alarm, alarm_id)
        if alarm is None or alarm.deleted_at is not None:
            missing += 1
            continue
        try:
            did_change = (
                await acknowledge_alarm(
                    session,
                    alarm,
                    acked_by=browser_session.operator_name,
                    note=reason,
                )
                if action == "ack"
                else await transition_alarm(
                    session,
                    alarm,
                    target_status=(
                        AlarmStatus.RESOLVED if action == "resolve" else AlarmStatus.CANCELLED
                    ),
                    actor=browser_session.operator_name,
                    note=reason,
                )
            )
        except Exception as exc:
            is_conflict = (
                getattr(exc, "status_code", None) == 409
                or exc.__class__.__name__ == "ConflictError"
            )
            if is_conflict:
                unchanged += 1
                continue
            raise
        changed += int(did_change)
        unchanged += int(not did_change)
    await set_flash(
        get_redis(request), browser_session, "success", f"bulk_{changed}_{unchanged}_{missing}"
    )
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/export")
async def admin_export(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    await _session_from_request(request, settings, admin_session, extend=True)
    from alarm_broker.api.routes.alarms import AlarmExportQuery, export_alarms
    from alarm_broker.api.schemas import ExportFormat

    return await export_alarms(
        AlarmExportQuery(
            status=AlarmStatus(status_filter) if status_filter else None,
            format=ExportFormat(format),
            limit=2000,
        ),
        session,
    )


_RESOURCE_MODELS: dict[str, Any] = {
    "sites": Site,
    "rooms": Room,
    "people": Person,
    "devices": Device,
}
_RESOURCE_FIELDS = {
    "sites": ("name",),
    "rooms": ("site_id", "label", "floor", "notes"),
    "people": ("display_name", "role", "phone_mobile", "phone_ext"),
    "devices": (
        "vendor",
        "model_family",
        "mac",
        "account_ext",
        "device_token",
        "person_id",
        "room_id",
    ),
}


def _resource_row(resource_name: str, item: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    masked: dict[str, str] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
        raw = getattr(item, field)
        if field == "device_token":
            values[field] = ""
            masked[field] = "••••" + raw[-4:] if raw else "—"
        else:
            values[field] = raw or ""
    return {
        "id": item.id,
        "version": item.version,
        "active": item.active,
        "values": values,
        "masked": masked,
    }


@router.get("/admin/configuration/{resource_name}", response_class=HTMLResponse)
async def admin_configuration_list(
    resource_name: str,
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    if resource_name == "escalation":
        return await admin_escalation_page(request, lang, admin_session, session, settings)
    if resource_name == "import":
        return await admin_import_page(request, lang, admin_session, settings)
    if resource_name not in _RESOURCE_MODELS:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    model: Any = _RESOURCE_MODELS[resource_name]
    items = list((await session.scalars(select(model).order_by(model.id))).all())
    return _html(
        request,
        "admin_resources.html",
        locale,
        resource_name=resource_name,
        fields=_RESOURCE_FIELDS[resource_name],
        resources=[_resource_row(resource_name, item) for item in items],
        save_action=f"/admin/configuration/{resource_name}/save",
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


@router.post("/admin/configuration/{resource_name}/save")
async def admin_configuration_save(
    resource_name: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    resource_id: str = Form(..., min_length=1, max_length=200),
    version: int | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    if resource_name not in _RESOURCE_MODELS:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    form = await request.form()
    model: Any = _RESOURCE_MODELS[resource_name]
    item: Any = await session.get(model, resource_id)
    creating = item is None
    if creating:
        item = model(id=resource_id)
        session.add(item)
    elif version is None:
        raise HTTPException(status_code=409, detail="version_required")
    else:
        require_current_version(item, version)

    changed: dict[str, Any] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
        submitted = str(form.get(field, "")).strip()
        if field == "device_token" and not submitted and not creating:
            continue
        if creating and field == "device_token" and not submitted:
            raise HTTPException(status_code=422, detail="device_token_required")
        value: str | None = submitted or None
        required_fields = {"name", "label", "display_name", "vendor", "model_family"}
        if field in required_fields and value is None:
            raise HTTPException(status_code=422, detail=f"{field}_required")
        setattr(item, field, value)
        changed[field] = value
    item.active = str(form.get("active", "")).lower() in {"1", "true", "on", "yes"}
    item.version = 1 if creating else item.version + 1
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="create" if creating else "update",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields={**changed, "active": item.active},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await set_flash(get_redis(request), browser_session, "success", "saved")
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


async def _active_dependency_counts(
    session: AsyncSession, resource_name: str, resource_id: str
) -> dict[str, int]:
    filters: dict[str, tuple[Any, Any]] = {
        "sites": (Room.id, (Room.site_id == resource_id) & Room.active.is_(True)),
        "rooms": (Device.id, (Device.room_id == resource_id) & Device.active.is_(True)),
        "people": (Device.id, (Device.person_id == resource_id) & Device.active.is_(True)),
    }
    if resource_name not in filters:
        return {}
    column, condition = filters[resource_name]
    count = int(await session.scalar(select(func.count(column)).where(condition)) or 0)
    return {"active_dependencies": count}


@router.post("/admin/configuration/{resource_name}/{resource_id}/deactivate")
async def admin_configuration_deactivate(
    resource_name: str,
    resource_id: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    version: int = Form(...),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    if resource_name not in _RESOURCE_MODELS:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    item: Any = await session.get(_RESOURCE_MODELS[resource_name], resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    require_current_version(item, version)
    blockers = await _active_dependency_counts(session, resource_name, resource_id)
    if any(blockers.values()):
        raise HTTPException(status_code=409, detail=blockers)
    item.active = False
    item.version += 1
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="deactivate",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields={"active": False},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


async def _historical_dependency_count(
    session: AsyncSession, resource_name: str, resource_id: str
) -> int:
    conditions: dict[str, list[tuple[Any, Any]]] = {
        "sites": [(Room.id, Room.site_id == resource_id), (Alarm.id, Alarm.site_id == resource_id)],
        "rooms": [
            (Device.id, Device.room_id == resource_id),
            (Alarm.id, Alarm.room_id == resource_id),
        ],
        "people": [
            (Device.id, Device.person_id == resource_id),
            (Alarm.id, Alarm.person_id == resource_id),
        ],
        "devices": [(Alarm.id, Alarm.device_id == resource_id)],
    }
    total = 0
    for column, condition in conditions[resource_name]:
        total += int(await session.scalar(select(func.count(column)).where(condition)) or 0)
    return total


@router.post("/admin/configuration/{resource_name}/{resource_id}/delete")
async def admin_configuration_delete(
    resource_name: str,
    resource_id: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    version: int = Form(...),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    if resource_name not in _RESOURCE_MODELS:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    item: Any = await session.get(_RESOURCE_MODELS[resource_name], resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    require_current_version(item, version)
    if await _historical_dependency_count(session, resource_name, resource_id):
        raise HTTPException(status_code=409, detail="resource_is_referenced_deactivate_instead")
    if item.active:
        raise HTTPException(status_code=409, detail="deactivate_before_delete")
    await session.delete(item)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="delete",
        resource_type=resource_name,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


@router.get("/admin/configuration/escalation", response_class=HTMLResponse)
async def admin_escalation_page(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    policy = await session.get(EscalationPolicy, "default")
    targets = list((await session.scalars(select(EscalationTarget))).all())
    steps = list(
        (
            await session.scalars(
                select(EscalationStep)
                .where(EscalationStep.policy_id == "default")
                .order_by(EscalationStep.step_no)
            )
        ).all()
    )
    payload = {
        "policy_id": "default",
        "name": policy.name if policy else "Default",
        "targets": [
            {
                "id": item.id,
                "label": item.label,
                "channel": item.channel,
                "address": "",
                "enabled": item.enabled,
            }
            for item in targets
        ],
        "steps": [
            {
                "step_no": step.step_no,
                "after_seconds": step.after_seconds,
                "target_ids": [step.target_id],
            }
            for step in steps
        ],
    }
    return _html(
        request,
        "admin_policy.html",
        locale,
        policy_json=json.dumps(payload, indent=2),
        policy_version=policy.version if policy else 0,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


@router.post("/admin/configuration/escalation")
async def admin_escalation_save(
    request: Request,
    policy_json: str = Form(..., max_length=100_000),
    version: int = Form(...),
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    try:
        body = EscalationPolicyIn.model_validate_json(policy_json)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_policy") from exc
    if body.policy_id != "default":
        raise HTTPException(status_code=422, detail="only_default_policy_is_editable")
    for target in body.targets:
        if target.address:
            continue
        existing_target = await session.get(EscalationTarget, target.id)
        if existing_target is None:
            raise HTTPException(status_code=422, detail="new_target_address_required")
        target.address = existing_target.address
    current = await session.get(EscalationPolicy, "default")
    if current is not None:
        require_current_version(current, version)
        current.version += 1
    elif version != 0:
        raise HTTPException(status_code=409, detail="policy_version_conflict")
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="update",
        resource_type="escalation_policy",
        resource_id="default",
        changed_fields={"policy": body.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await apply_escalation_policy(session, body)
    return RedirectResponse("/admin/configuration/escalation", status_code=303)


@router.get("/admin/configuration/import", response_class=HTMLResponse)
async def admin_import_page(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    return _html(
        request,
        "admin_import.html",
        locale,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        preview=None,
        seed_text="",
    )


@router.post("/admin/configuration/import", response_class=HTMLResponse)
async def admin_import_submit(
    request: Request,
    seed_text: str = Form(..., max_length=1_048_576),
    action: str = Form(..., pattern="^(preview|apply)$"),
    content_hash: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    locale = _requested_locale(request, None)
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    raw = seed_text.encode()
    data = parse_seed_payload("application/x-yaml", raw)
    digest = hashlib.sha256(raw).hexdigest()
    if action == "preview":
        return _html(
            request,
            "admin_import.html",
            locale,
            csrf_token=browser_session.csrf_token,
            operator_name=browser_session.operator_name,
            logout_action="/admin/logout",
            preview={"hash": digest, "sections": sorted(data)},
            seed_text=seed_text,
        )
    if content_hash is None or not secrets.compare_digest(content_hash, digest):
        raise HTTPException(status_code=409, detail="import_preview_is_stale")
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="import",
        resource_type="configuration",
        resource_id=digest,
        changed_fields={"content_hash": digest, "sections": sorted(data)},
        request_id=getattr(request.state, "request_id", None),
    )
    await apply_seed_payload(session, data=data, settings=settings)
    return RedirectResponse("/admin/configuration/import", status_code=303)


@router.get("/admin/activity", response_class=HTMLResponse)
async def admin_activity(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    events = list(
        (
            await session.scalars(
                select(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc()).limit(100)
            )
        ).all()
    )
    return _html(
        request,
        "admin_activity.html",
        locale,
        events=events,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.get("/admin/system", response_class=HTMLResponse)
async def admin_system(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    database_ok = (await session.scalar(select(func.count(Alarm.id)))) is not None
    states = [
        {"name": "Application", "status": "ok", "detail": __version__},
        {"name": "Database", "status": "ok" if database_ok else "error", "detail": "query"},
        {"name": "Redis", "status": "ok", "detail": "operator session"},
        {
            "name": "Simulation",
            "status": "enabled" if settings.simulation_enabled else "disabled",
            "detail": "SIMULATION_ENABLED",
        },
    ]
    return _html(
        request,
        "admin_system.html",
        locale,
        states=states,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.get("/admin/simulation", response_class=HTMLResponse)
async def admin_simulation(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    if not settings.simulation_enabled:
        raise HTTPException(status_code=404, detail="simulation_disabled")
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    notifications = get_mock_store().get_all()
    return _html(
        request,
        "admin_simulation.html",
        locale,
        notifications=notifications,
        clear_action="/admin/simulation/clear",
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.post("/admin/simulation/clear")
async def admin_simulation_clear(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    if not settings.simulation_enabled:
        raise HTTPException(status_code=404, detail="simulation_disabled")
    await _action_session(request, settings, admin_session, csrf_token)
    get_mock_store().clear()
    return RedirectResponse("/admin/simulation", status_code=303)


def _render_alarm_row(alarm: Alarm) -> str:
    """Legacy unit-test seam; the live worklist is rendered by Jinja."""
    alarm_id = str(alarm.id)
    can_ack = alarm.status == AlarmStatus.TRIGGERED
    can_resolve = alarm.status in {AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED}
    disabled_ack = " disabled" if not can_ack else ""
    disabled_resolve = " disabled" if not can_resolve else ""
    return (
        f"<tr data-alarm-id='{escape(alarm_id)}' "
        f"data-person='{escape(str(alarm.person_id or '-'))}' "
        f"data-room='{escape(str(alarm.room_id or '-'))}' data-source='{escape(alarm.source)}' "
        f"data-severity='{escape(alarm.severity)}' "
        f"data-acked-by='{escape(str(alarm.acked_by or '-'))}' "
        f"data-can-ack='{'true' if can_ack else 'false'}' "
        f"data-can-resolve='{'true' if can_resolve else 'false'}'>"
        f"<td>{escape(alarm.status.value)}</td><td>{escape(alarm_id[:8])}</td>"
        f"<td><button class='quick-ack-btn'{disabled_ack}>Quick Ack</button>"
        f"<button class='quick-resolve-btn'{disabled_resolve}>Quick Resolve</button></td></tr>"
    )
