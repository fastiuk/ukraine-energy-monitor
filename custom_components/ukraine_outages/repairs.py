"""Repairs. Fixable issues: an address moved to another group (confirming dismisses it), and a
group chosen before the operator renumbered its groups (confirming keeps it, for good)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_GROUP_CHECKED


class GroupCheckedFlow(ConfirmRepairFlow):
    """The user checked the group after the operator renumbered its groups: remember it on the
    entry, so the issue does not come back with the next start. The form is the issue's own."""

    def __init__(self, entry_id: str, since: str) -> None:
        super().__init__()
        self.entry_id = entry_id
        self.since = since

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if entry is not None:
                self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_GROUP_CHECKED: self.since})
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None) -> RepairsFlow:
    if issue_id.startswith("group_renumbered_") and data and isinstance(data.get("entry_id"), str) and isinstance(data.get("since"), str):
        return GroupCheckedFlow(str(data["entry_id"]), str(data["since"]))
    return ConfirmRepairFlow()
