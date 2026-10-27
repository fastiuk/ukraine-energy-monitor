from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OutageCoordinator, OutagesConfigEntry
from .entity import OutageEntity, day_outages
from .ua_outages import KYIV

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: OutagesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [OutageBinarySensor(coordinator), PossibleOutageBinarySensor(coordinator), SourceBinarySensor(coordinator)]
    )


def _now_in(coordinator: OutageCoordinator, *, possible: bool) -> bool:
    now = dt_util.now()
    return any(o.start <= now < o.end and o.possible == possible for o in coordinator.outages())


class OutageBinarySensor(OutageEntity, BinarySensorEntity):
    """On while a scheduled outage is in progress."""

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "outage")

    @property
    def is_on(self) -> bool:
        return _now_in(self.coordinator, possible=False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        today = dt_util.now(KYIV).date()
        data = coordinator.data or {}
        group = coordinator.group
        days = {"today": data.get(today), "tomorrow": data.get(today + timedelta(days=1))}
        attributes: dict[str, Any] = {"group": group}
        for name, schedule in days.items():
            attributes[name] = day_outages(schedule, group, possible=False)
            attributes[f"{name}_possible"] = day_outages(schedule, group, possible=True)
        return attributes


class PossibleOutageBinarySensor(OutageEntity, BinarySensorEntity):
    """On while the schedule says power may be off (only some operators publish this)."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "possible_outage")

    @property
    def is_on(self) -> bool:
        return _now_in(self.coordinator, possible=True)


class SourceBinarySensor(OutageEntity, BinarySensorEntity):
    """On while the operator's source answers; stays available when it does not (that is the news).

    No connectivity device class: its «Відключено» would read as «no power». The states are its own
    («Відповідає» / «Не відповідає»)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "source_ok")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "source": c.last_source,
            "last_success": c.last_success.isoformat() if c.last_success else None,
            "failing_since": c.failing_since.isoformat() if c.failing_since else None,
            "last_error": c.last_error,
        }
