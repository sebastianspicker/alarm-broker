from __future__ import annotations

from html import escape
from pathlib import Path
from string import Template
from typing import Any

from alarm_broker.db.models import Alarm, AlarmStatus

_TEMPLATE = Template(
    Path(__file__)
    .resolve()
    .parents[1]
    .joinpath("api", "templates", "ack.html")
    .read_text(encoding="utf-8")
)


def render_ack_page(alarm: Alarm, enriched: dict[str, Any]) -> str:
    person = escape(str(enriched.get("person_name") or (alarm.person_id or "-")), quote=True)
    room = escape(str(enriched.get("room_label") or (alarm.room_id or "-")), quote=True)
    created = escape(alarm.created_at.isoformat(), quote=True)
    status_label = escape(alarm.status.value, quote=True)

    is_triggered = alarm.status == AlarmStatus.TRIGGERED

    form_block = (
        """
    <form method=\"post\"> 
      <label for=\"acked_by\">Dein Name (optional)
        <input id=\"acked_by\" name=\"acked_by\" autocomplete=\"name\">
      </label>
      <label for=\"note\">Notiz (optional)
        <textarea id=\"note\" name=\"note\" rows=\"4\"></textarea>
      </label>
      <button type=\"submit\">Alarm übernehmen</button>
    </form>
"""
        if is_triggered
        else ""
    )

    return _TEMPLATE.substitute(
        title="Alarm übernehmen" if is_triggered else "Alarm",
        headline="Alarm übernehmen" if is_triggered else "Alarm",
        status_label=status_label,
        status_color="#b45309" if is_triggered else "#047857",
        person=person,
        room=room,
        created=created,
        info_message=(
            "Bitte quittiere den Alarm, wenn du die Übernahme bestätigst."
            if is_triggered
            else "Dieser Alarm wurde bereits bearbeitet."
        ),
        form_block=form_block,
    )
