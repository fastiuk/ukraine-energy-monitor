"""The schedule event, the source sensor, Repairs and the card's data."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ukraine_outages.const import CONF_ADDRESS, CONF_ADDRESS_LABEL, CONF_GROUP, CONF_OPERATOR, DOMAIN
from custom_components.ukraine_outages.ua_outages import DaySchedule, FetchError, Outage

from .conftest import DAY, GROUP, OPERATOR, UNIQUE_ID, entity_id, kyiv, schedules

TOMORROW = DAY + timedelta(days=1)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, freezer, moment) -> None:
    """Move to ``moment``, past the next poll (every 5 minutes)."""
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()


async def test_schedule_event_for_published_and_changed_days(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)
    event = entity_id(hass, "event", "schedule")
    assert hass.states.get(event).state == "unknown"  # the first fetch announces nothing

    published = {**schedules(), TOMORROW: DaySchedule(TOMORROW, kyiv(18), {GROUP: (Outage(kyiv(8, day=18), kyiv(10, day=18)),)})}
    mock_fetch.return_value = published
    await _poll(hass, freezer, kyiv(9, 6))
    state = hass.states.get(event)
    assert state.attributes["event_type"] == "schedule_published"
    assert state.attributes["day"] == "tomorrow" and state.attributes["date"] == "2026-10-18"
    assert state.attributes["outages"] == ["08:00–10:00"] and state.attributes["possible"] == []
    assert "previous" not in state.attributes
    fired = state.state

    changed = {**published, DAY: DaySchedule(DAY, kyiv(9, 10), {GROUP: (Outage(kyiv(11), kyiv(13)),), "1.1": ()})}
    mock_fetch.return_value = changed
    await _poll(hass, freezer, kyiv(9, 12))
    state = hass.states.get(event)
    assert state.attributes["event_type"] == "schedule_changed" and state.attributes["day"] == "today"
    assert state.attributes["outages"] == ["11:00–13:00"]
    assert state.attributes["previous"] == ["10:00–12:00", "21:00–22:30"]
    assert state.state != fired
    fired = state.state

    # An outage starting (an update without a fetch) and another group's change fire nothing.
    other = {**changed, DAY: DaySchedule(DAY, kyiv(9, 10), {GROUP: (Outage(kyiv(11), kyiv(13)),), "1.1": (Outage(kyiv(14), kyiv(15)),)})}
    mock_fetch.return_value = other
    await _poll(hass, freezer, kyiv(11))
    assert hass.states.get(event).state == fired


async def test_source_sensor_and_the_repairs_issue(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)
    sensor = entity_id(hass, "binary_sensor", "source_ok")
    assert hass.states.get(sensor).state == "on"
    assert hass.states.get(sensor).attributes["last_success"]

    mock_fetch.side_effect = FetchError("GET https://example.invalid/x: HTTP 503")
    await _poll(hass, freezer, kyiv(9, 6))
    state = hass.states.get(sensor)
    assert state.state == "off" and "HTTP 503" in state.attributes["last_error"] and state.attributes["failing_since"]
    # The schedule itself stays: it is still the best there is.
    assert hass.states.get(entity_id(hass, "binary_sensor", "outage")).state == "off"
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"source_unavailable_{OPERATOR}") is None

    moment = kyiv(9, 6)
    for _ in range(40):  # past three hours of failures
        moment += timedelta(minutes=5)
        await _poll(hass, freezer, moment)
    issue = issues.async_get_issue(DOMAIN, f"source_unavailable_{OPERATOR}")
    assert issue is not None and not issue.is_fixable and issue.translation_key == "source_unavailable"

    mock_fetch.side_effect = None
    await _poll(hass, freezer, moment + timedelta(minutes=5))
    assert hass.states.get(sensor).state == "on"
    assert issues.async_get_issue(DOMAIN, f"source_unavailable_{OPERATOR}") is None


async def test_the_error_shown_carries_no_address_and_no_markup(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    """A per-address source's query holds the address; a mirror's answer may hold Markdown."""
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)
    mock_fetch.side_effect = FetchError("GET https://example.invalid/x?street=Руська&house=1: HTTP 503 `![x](https://evil.invalid/p.png)`")
    moment = kyiv(9)
    for _ in range(40):
        moment += timedelta(minutes=5)
        await _poll(hass, freezer, moment)
    error = hass.states.get(entity_id(hass, "binary_sensor", "source_ok")).attributes["last_error"]
    assert "Руська" not in error and "x?…" in error
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"source_unavailable_{OPERATOR}")
    assert "`" not in issue.translation_placeholders["error"]  # the translation puts it in a code span


async def test_a_moved_address_is_followed_and_reported(kyiv_hass, freezer, mock_fetch) -> None:
    hass = kyiv_hass
    assert await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Тернопіль, вул. Руська, 1 · черга 3.2",
        data={CONF_OPERATOR: "ternopil-oe", CONF_GROUP: "3.2", CONF_ADDRESS: "1032/10035/1", CONF_ADDRESS_LABEL: "Тернопіль, вул. Руська, 1"},
        unique_id="ternopil-oe:1032/10035/1",
    )
    mock_fetch.return_value = {DAY: DaySchedule(DAY, None, {"3.2": (Outage(kyiv(10), kyiv(12)),)})}
    freezer.move_to(kyiv(9))
    await _setup(hass, entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"address_moved_{entry.entry_id}") is None

    mock_fetch.return_value = {DAY: DaySchedule(DAY, None, {"4.1": (Outage(kyiv(14), kyiv(16)),)})}
    await _poll(hass, freezer, kyiv(9, 6))
    await hass.async_block_till_done()
    assert entry.data[CONF_GROUP] == "4.1" and entry.title == "Тернопіль, вул. Руська, 1 · черга 4.1"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"address_moved_{entry.entry_id}")
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {"address": "Тернопіль, вул. Руська, 1", "old": "3.2", "new": "4.1", "label": "черга"}
    outage = entity_id(hass, "binary_sensor", "outage", "ternopil-oe:1032/10035/1")
    assert hass.states.get(outage).attributes["today"] == ["14:00–16:00"]
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.unique_id), entry.entry_id)
    assert device.name == "Тернопіль, вул. Руська, 1 · черга 4.1" and device.model == "Черга 4.1"

    # Confirming in Repairs dismisses it.
    flows = hass.data["repairs"]["flow_manager"]
    result = await flows.async_init(DOMAIN, data={"issue_id": issue.issue_id})
    assert result["step_id"] == "confirm"
    result = await flows.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"address_moved_{entry.entry_id}") is None


async def test_removing_the_last_entry_removes_its_issues(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)
    ir.async_create_issue(hass, DOMAIN, f"source_unavailable_{OPERATOR}", is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="source_unavailable")
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"source_unavailable_{OPERATOR}") is None


async def test_card_data(kyiv_hass, freezer, mock_fetch, config_entry, hass_ws_client) -> None:
    hass = kyiv_hass
    # The web server starts with the client: whatever registers views (repairs) comes first.
    assert await async_setup_component(hass, "repairs", {})
    client = await hass_ws_client(hass)  # signed in before the clock moves: its token stays valid
    freezer.move_to(kyiv(10, 30))
    await _setup(hass, config_entry)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, UNIQUE_ID), config_entry.entry_id)

    await client.send_json({"id": 1, "type": "ukraine_outages/card", "device_id": device.id})
    reply = await client.receive_json()
    assert reply["success"], reply
    data = reply["result"]
    assert data["title"] == "Київська · черга 3.2" and data["group"] == GROUP and data["group_label"] == "черга"
    assert data["now_minute"] == 630
    today, tomorrow = data["days"]
    assert today["date"] == "2026-10-17" and today["published"]
    assert today["outages"] == [
        {"from": 600, "to": 720, "possible": False},
        {"from": 720, "to": 750, "possible": True},
        {"from": 1260, "to": 1350, "possible": False},
    ]
    assert tomorrow == {"date": "2026-10-18", "published": False, "outages": [], "unknown": None, "notes": [], "image_url": None, "updated_at": None}
    assert data["current"]["end"].startswith("2026-10-17T12:00") and not data["current"]["possible"]
    assert data["source"]["ok"] is True

    await client.send_json({"id": 2, "type": "ukraine_outages/card", "device_id": "nope"})
    reply = await client.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "not_found"
