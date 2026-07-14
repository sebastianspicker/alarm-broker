"""Shared optimistic-concurrency and lifecycle guards for master data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.core.errors import ConflictError
from alarm_broker.db.models import Person, Room, Site


class VersionedResource(Protocol):
    id: str
    version: int


class VersionedMasterData(VersionedResource, Protocol):
    active: bool


def require_current_version(resource: VersionedResource, expected_version: int) -> None:
    """Reject an edit based on a stale representation of a resource."""
    if resource.version != expected_version:
        raise ConflictError(
            "Resource has changed since it was loaded",
            details={
                "resource_id": resource.id,
                "expected_version": expected_version,
                "current_version": resource.version,
            },
        )


def require_no_dependencies(
    resource: VersionedMasterData, dependency_counts: Mapping[str, int]
) -> None:
    """Reject deactivation while active dependants still reference a resource."""
    dependencies = {name: count for name, count in dependency_counts.items() if count > 0}
    if dependencies:
        raise ConflictError(
            "Resource cannot be deactivated while dependencies remain",
            details={"resource_id": resource.id, "dependencies": dependencies},
        )


def deactivate_resource(
    resource: VersionedMasterData,
    *,
    expected_version: int,
    dependency_counts: Mapping[str, int] | None = None,
) -> bool:
    """Deactivate a resource and increment its version, if its contract permits it.

    Returns ``False`` for an idempotent deactivate request.  A caller should pass
    counts for active dependants when that resource type has dependency rules.
    """
    require_current_version(resource, expected_version)
    if not resource.active:
        return False
    require_no_dependencies(resource, dependency_counts or {})
    resource.active = False
    resource.version += 1
    return True


async def lock_active_referenced_parents(
    session: AsyncSession,
    *,
    resource_name: str,
    values: dict[str, Any],
) -> None:
    """Lock and validate parents before an active child write.

    A parent deactivation uses ``FOR UPDATE`` before counting dependants. Child
    writers acquire the same lock before they insert or update an active Room
    or Device. On PostgreSQL this makes the two transactions serialize: the
    child either commits first and is counted, or observes the inactive parent
    after deactivation commits and fails with a controlled conflict. SQLite
    treats ``FOR UPDATE`` as a no-op, so it can only cover the inactive-parent
    response path deterministically.
    """
    if not values.get("active", True):
        return

    references: dict[str, tuple[tuple[str, type[Any]], ...]] = {
        "rooms": (("site_id", Site),),
        # Keep a stable lock order for a Device that references both parents.
        "devices": (("person_id", Person), ("room_id", Room)),
    }
    for field, model in references.get(resource_name, ()):
        parent_id = values.get(field)
        if parent_id is None:
            continue
        parent = await session.scalar(select(model).where(model.id == parent_id).with_for_update())
        if parent is None or not parent.active:
            raise ConflictError(
                "Active resource cannot reference an inactive or missing parent",
                details={
                    "resource_type": resource_name,
                    "parent_field": field,
                    "parent_id": parent_id,
                },
            )
