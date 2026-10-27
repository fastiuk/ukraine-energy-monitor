"""Fetches one group's schedule and wakes entities up at outage boundaries.

It also remembers what changed in the last fetch (for the schedule event), how the source is
doing (for the diagnostic sensor), and raises Repairs issues: a source that has failed for
``SOURCE_ISSUE_AFTER_HOURS``, and an address the operator moved to another group.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDRESS,
    CONF_ADDRESS_LABEL,
    CONF_GROUP,
    CONF_OPERATOR,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SOURCE_ISSUE_AFTER_HOURS,
    STATUS_PAGE,
)
from .hub import SourceHub
from .ua_outages import (
    KYIV,
    DayChange,
    DaySchedule,
    FetchError,
    Operator,
    Outage,
    diff_schedules,
    group_events,
    group_outages,
    merge_schedules,
)
from .ua_outages import operator as find_operator
from .ua_outages.http import redact
from .ua_outages.registry import REGIONS_BY_ID

_LOGGER = logging.getLogger(__name__)


type OutagesConfigEntry = ConfigEntry[OutageCoordinator]


def entry_title(op: Operator, group: str, address_label: str | None = None) -> str:
    """"м. Київ · група 19.1", "Дніпропетровська · ЦЕК · черга 3.2", "Тернопіль, вул. Руська, 1 · черга 3.2"."""
    region = REGIONS_BY_ID[op.region]
    where = address_label or (region.name if len(region.operators) == 1 else f"{region.name} · {op.name}")
    return f"{where} · {op.group_label} {group}"


def source_issue_id(op: Operator) -> str:
    return f"source_unavailable_{op.id}"


def moved_issue_id(entry_id: str) -> str:
    return f"address_moved_{entry_id}"


def scan_minutes(op: Operator, requested: int) -> int:
    """The requested interval, but never more often than the operator's sources allow."""
    return max(requested, -(-op.poll_interval // 60), 1)


class OutageCoordinator(DataUpdateCoordinator[dict[date, DaySchedule]]):
    config_entry: OutagesConfigEntry

    def __init__(self, hass: HomeAssistant, entry: OutagesConfigEntry, hub: SourceHub) -> None:
        op = find_operator(entry.data[CONF_OPERATOR])
        if op is None or not op.available:
            raise ValueError(f"Unknown or unavailable operator {entry.data[CONF_OPERATOR]!r}")
        self.operator = op
        self.group: str = entry.data[CONF_GROUP]
        self.address: str | None = entry.data.get(CONF_ADDRESS)
        self.hub = hub
        minutes = scan_minutes(op, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {op.id} {self.group}",
            update_interval=timedelta(minutes=minutes),
        )
        self._unsub_transition: CALLBACK_TYPE | None = None
        #: This group's changes in the last successful fetch (none on the first one), and how
        #: many fetches succeeded: entities also update at outage boundaries, without a fetch.
        self.changes: list[DayChange] = []
        self.fetches = 0
        self.last_success: datetime | None = None
        #: When the source started failing; None while it answers.
        self.failing_since: datetime | None = None
        self.last_error: str | None = None

    @property
    def last_source(self) -> str | None:
        return self.hub.last_source.get((self.operator.id, self.address))

    async def _async_update_data(self) -> dict[date, DaySchedule]:
        try:
            fresh = await self.hub.fetch(self.operator, self.address)
        except FetchError as err:
            self._failed(str(err))
            raise UpdateFailed(str(err)) from err
        self.last_success, self.failing_since, self.last_error = dt_util.utcnow(), None, None
        ir.async_delete_issue(self.hass, DOMAIN, source_issue_id(self.operator))
        if self.operator.by_address:
            self._follow_address_group(fresh)
        # Keeps days and groups a fetch lacks (sites sometimes serve a half-edited page)
        # and drops past days.
        merged = merge_schedules(self.data or {}, fresh, since=dt_util.now(KYIV).date())
        # Nothing to compare with on the first fetch (after a restart too): no "changes" then.
        old = self.data
        self.changes = [] if old is None else [c for c in diff_schedules(old, merged) if self.group in c.groups]
        self.fetches += 1
        return merged

    def _failed(self, error: str) -> None:
        now = dt_util.utcnow()
        self.failing_since = self.failing_since or now
        self.last_error = redact(error)[:300]  # a per-address source's query holds the address
        if now - self.failing_since < timedelta(hours=SOURCE_ISSUE_AFTER_HOURS):
            return
        op = self.operator
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            source_issue_id(op),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="source_unavailable",
            translation_placeholders={
                "operator": op.name,
                "since": f"{dt_util.as_local(self.failing_since):%d.%m %H:%M}",
                # Repairs renders Markdown: in a code span the site's words stay text, not links or images.
                "error": " ".join(self.last_error.replace("`", "'").split())[:200],
            },
            learn_more_url=STATUS_PAGE,
        )

    def _follow_address_group(self, fresh: dict[date, DaySchedule]) -> None:
        """The operator moved the address to another group: follow it, as the bot does, and
        say so in Repairs (automations may name the old group)."""
        groups = {g for s in fresh.values() for g in s.mentioned_groups()}
        if len(groups) != 1 or self.group in groups:
            return
        new = groups.pop()
        if new not in self.operator.groups:
            return
        old, entry, op = self.group, self.config_entry, self.operator
        _LOGGER.info("%s: the address moved from group %s to %s", op.id, old, new)
        self.group = new
        label = entry.data.get(CONF_ADDRESS_LABEL)
        title = entry_title(op, new, label)
        self.hass.config_entries.async_update_entry(entry, title=title, data={**entry.data, CONF_GROUP: new})
        # The device was named for the old group (none yet on the first fetch: it is set up below).
        devices = dr.async_get(self.hass)
        identifier = (DOMAIN, entry.unique_id or entry.entry_id)
        if hasattr(devices, "async_get_device_by_identifier"):
            device = devices.async_get_device_by_identifier(identifier, entry.entry_id)
        else:  # older Home Assistant
            device = devices.async_get_device(identifiers={identifier})
        if device:
            devices.async_update_device(device.id, name=title, model=f"{op.group_label.capitalize()} {new}")
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            moved_issue_id(entry.entry_id),
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="address_moved",
            translation_placeholders={"address": label or op.name, "old": old, "new": new, "label": op.group_label},
            data={"entry_id": entry.entry_id},
        )

    @property
    def schedules(self) -> list[DaySchedule]:
        return [self.data[day] for day in sorted(self.data)] if self.data else []

    def outages(self, *, possible: bool = True) -> tuple[Outage, ...]:
        """This group's outages across known days, joined across midnight."""
        return group_outages(self.schedules, self.group, possible=possible)

    @callback
    def async_update_listeners(self) -> None:
        super().async_update_listeners()
        self._schedule_transition()

    @callback
    def _schedule_transition(self) -> None:
        """Refresh entities exactly when an outage starts or ends, not at the next poll."""
        self.cancel_transition()
        now = dt_util.now()
        upcoming = [e.at for e in group_events(self.schedules, self.group, possible=True) if e.at > now]
        if upcoming:
            self._unsub_transition = async_track_point_in_time(self.hass, self._handle_transition, min(upcoming))

    @callback
    def _handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        self.async_update_listeners()

    @callback
    def cancel_transition(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None
