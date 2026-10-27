"""The schedule as an event: «published» for a newly published day, «changed» for a known one."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OutageCoordinator, OutagesConfigEntry
from .entity import OutageEntity, day_outages
from .ua_outages import KYIV, DayChange

PARALLEL_UPDATES = 0
PUBLISHED = "schedule_published"
CHANGED = "schedule_changed"


async def async_setup_entry(
    hass: HomeAssistant, entry: OutagesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ScheduleEvent(entry.runtime_data)])


def event_data(change: DayChange, group: str) -> dict[str, Any]:
    """What an automation needs: which day, and the group's outages now (and before)."""
    today = dt_util.now(KYIV).date()
    day = change.schedule.day
    data: dict[str, Any] = {
        "day": "today" if day == today else "tomorrow" if day == today + timedelta(days=1) else "other",
        "date": day.isoformat(),
        "outages": day_outages(change.schedule, group, possible=False),
        "possible": day_outages(change.schedule, group, possible=True),
    }
    if change.previous is not None:
        data["previous"] = day_outages(change.previous, group, possible=False)
        data["previous_possible"] = day_outages(change.previous, group, possible=True)
    return data


class ScheduleEvent(OutageEntity, EventEntity):
    _attr_event_types = [PUBLISHED, CHANGED]

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "schedule")
        # Changes are read once per fetch: entities also update at outage boundaries.
        self._seen = coordinator.fetches

    @callback
    def _handle_coordinator_update(self) -> None:
        coordinator = self.coordinator
        if coordinator.fetches != self._seen:
            self._seen = coordinator.fetches
            for change in coordinator.changes:
                self._trigger_event(PUBLISHED if change.published else CHANGED, event_data(change, coordinator.group))
                self.async_write_ha_state()
        super()._handle_coordinator_update()
