"""The dashboard card: its script, served by the integration, and the data it asks for.

The card (``frontend/ukraine-outages-card.js``) loads on every dashboard page, so there is
nothing to install. It asks ``ukraine_outages/card`` for a device's group schedule: minutes from
Kyiv midnight for each outage of today and tomorrow, the outage in progress and the next one,
and how the source is doing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import OutageCoordinator
from .ua_outages import KYIV, Outage, current_outage, next_outage

URL = "/ukraine_outages/ukraine-outages-card.js"
SCRIPT = Path(__file__).parent / "frontend" / "ukraine-outages-card.js"


async def async_register(hass: HomeAssistant) -> None:
    """Serve the card and add it to every dashboard page (skipped without a web frontend, as in tests)."""
    websocket_api.async_register_command(hass, ws_card)
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths([StaticPathConfig(URL, str(SCRIPT), True)])
    add_extra_js_url(hass, f"{URL}?v={version}")


def _minutes(moment: datetime, midnight: datetime) -> int:
    return max(0, min(1440, round((moment - midnight).total_seconds() / 60)))


def _outage(outage: Outage | None) -> dict[str, Any] | None:
    if outage is None:
        return None
    return {"start": outage.start.isoformat(), "end": outage.end.isoformat(), "possible": outage.possible}


def card_data(coordinator: OutageCoordinator) -> dict[str, Any]:
    entry, op, group = coordinator.config_entry, coordinator.operator, coordinator.group
    now = dt_util.now()
    today = now.astimezone(KYIV).date()
    days = []
    for offset in (0, 1):
        day = today + timedelta(days=offset)
        midnight = datetime(day.year, day.month, day.day, tzinfo=KYIV)
        schedule = (coordinator.data or {}).get(day)
        days.append(
            {
                "date": day.isoformat(),
                "published": schedule is not None,
                "outages": [
                    {"from": _minutes(o.start, midnight), "to": _minutes(o.end, midnight), "possible": o.possible}
                    for o in (schedule.outages(group) if schedule else ())
                ],
                # The group's hours could not be read (some operators publish them only as a picture).
                "unknown": schedule.unknown.get(group) if schedule else None,
                "notes": list(schedule.notes) if schedule else [],
                "image_url": schedule.image_url if schedule and (schedule.image_url or "").startswith("https://") else None,
                "updated_at": schedule.updated_at.isoformat() if schedule and schedule.updated_at else None,
            }
        )
    current = current_outage(coordinator.schedules, group, now)
    return {
        "title": entry.title,
        "operator": op.name,
        "group": group,
        "group_label": op.group_label,
        "now": now.isoformat(),
        "now_minute": _minutes(now, datetime(today.year, today.month, today.day, tzinfo=KYIV)),
        "days": days,
        "current": _outage(current),
        "next": _outage(next_outage(coordinator.schedules, group, now)),
        "source": {
            "ok": coordinator.last_update_success,
            "name": coordinator.last_source,
            "last_success": coordinator.last_success.isoformat() if coordinator.last_success else None,
        },
    }


@websocket_api.websocket_command({vol.Required("type"): "ukraine_outages/card", vol.Required("device_id"): str})
@callback
def ws_card(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    device = dr.async_get(hass).async_get(msg["device_id"])
    entry = next(
        (
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if device is not None and e.entry_id in device.config_entries and e.state is ConfigEntryState.LOADED
        ),
        None,
    )
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No outage schedule for this device")
        return
    connection.send_result(msg["id"], card_data(entry.runtime_data))
