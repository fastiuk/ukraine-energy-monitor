"""Hourly outage schedule (графік погодинних відключень) of one group, for any region of Ukraine."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.hass_dict import HassKey

from . import card
from .const import CONF_OPERATOR, DOMAIN
from .coordinator import OutageCoordinator, OutagesConfigEntry, moved_issue_id, source_issue_id
from .hub import SourceHub
from .ua_outages import operator as find_operator
from .ua_outages.http import ssl_context

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.EVENT, Platform.SENSOR]
HUB: HassKey[SourceHub] = HassKey(DOMAIN)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The dashboard card and the data it asks for (``card``)."""
    await card.async_register(hass)
    return True


async def get_hub(hass: HomeAssistant) -> SourceHub:
    """The shared hub, created on first use (config flow or the first entry)."""
    if HUB not in hass.data:
        # Reads CA bundles from disk: never on the event loop. Cached for every later request.
        await hass.async_add_executor_job(ssl_context)
        # Checked again: entries set up together all waited above, and they must share one hub.
        if HUB not in hass.data:
            hass.data[HUB] = SourceHub(hass)
    return hass.data[HUB]


async def async_setup_entry(hass: HomeAssistant, entry: OutagesConfigEntry) -> bool:
    coordinator = OutageCoordinator(hass, entry, await get_hub(hass))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.cancel_transition)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OutagesConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: OutagesConfigEntry) -> None:
    """Its Repairs issues go with it; the source's one too once no entry of the operator is left."""
    ir.async_delete_issue(hass, DOMAIN, moved_issue_id(entry.entry_id))
    op = find_operator(entry.data.get(CONF_OPERATOR, ""))
    others = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id and e.data.get(CONF_OPERATOR) == entry.data.get(CONF_OPERATOR)]
    if op is not None and not others:
        ir.async_delete_issue(hass, DOMAIN, source_issue_id(op))
