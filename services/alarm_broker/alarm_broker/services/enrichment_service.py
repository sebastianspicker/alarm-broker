from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import Alarm, Person, Room, Site


async def enrich_alarm_context(session: AsyncSession, alarm: Alarm) -> dict[str, Any]:
    enriched: dict[str, Any] = {}
    person_name = alarm.person_id
    room_label = alarm.room_id
    site_name = alarm.site_id

    if alarm.person_id:
        person = await session.get(Person, alarm.person_id)
        if person:
            person_name = person.display_name
    if alarm.room_id:
        room = await session.get(Room, alarm.room_id)
        if room:
            room_label = room.label
            site_name = room.site_id
            site = await session.get(Site, room.site_id)
            if site:
                site_name = site.name

    enriched["person_name"] = person_name
    enriched["room_label"] = room_label
    enriched["site_name"] = site_name
    return enriched
