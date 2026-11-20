import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import HomeAssistant

from custom_components.ukraine_outages import HUB, get_hub
from custom_components.ukraine_outages.hub import SourceHub
from custom_components.ukraine_outages.ua_outages import DaySchedule, FetchError, operator, now_kyiv


@dataclass
class FakeSource:
    name: str
    answer: dict
    delay: float = 0
    hosts: frozenset[str] = frozenset({"example.com"})

    async def fetch(self, http, now):
        await asyncio.sleep(self.delay)
        return self.answer


async def test_entries_set_up_together_share_one_hub(hass: HomeAssistant) -> None:
    hubs = await asyncio.gather(*(get_hub(hass) for _ in range(5)))
    assert all(hub is hubs[0] for hub in hubs)


async def test_slow_source_gives_way_and_answers_are_trimmed(hass: HomeAssistant) -> None:
    today = now_kyiv().date()
    days = {today + timedelta(days=n): DaySchedule(today + timedelta(days=n), None, {"1.1": (), "x": ()}) for n in range(-1, 30)}
    slow, good = FakeSource("slow", {}, delay=60), FakeSource("good", days)
    op = replace(operator("dtek-krem"), sources=(slow, good))
    with patch("custom_components.ukraine_outages.hub.SOURCE_DEADLINE", 0.05):
        result = await SourceHub(hass).fetch(op)
    assert list(result) == [today + timedelta(days=n) for n in range(3)]
    assert all(list(s.groups) == ["1.1"] for s in result.values())


async def test_the_session_outlives_the_entry_that_created_it(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    """HA closes a session made while an entry is set up once that entry fails or unloads. The hub's
    session serves every entry: on 20.11.2026 the network was not up yet when HA started, the first
    entry failed, and all 309 entries of the test HA kept failing with "Session is closed"."""
    mock_fetch.side_effect = FetchError("no network yet")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    hub = hass.data[HUB]
    assert not hub.session.closed

    mock_fetch.side_effect = None
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert hass.data[HUB] is hub and not hub.session.closed

    hass.bus.async_fire(EVENT_HOMEASSISTANT_CLOSE)
    await hass.async_block_till_done()
    assert hub.session.closed
