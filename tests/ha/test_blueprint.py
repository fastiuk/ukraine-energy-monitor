"""The notifications blueprint, as an automation built from it would run."""

import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.ukraine_outages.ua_outages import DaySchedule, Outage

from .conftest import DAY, GROUP, entity_id, kyiv, schedules, timedelta

BLUEPRINT = Path(__file__).parent.parent.parent / "blueprints" / "automation" / "ukraine_outages" / "notifications.yaml"


async def _at(hass: HomeAssistant, freezer, moment) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()


async def test_notifications_blueprint(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    target = Path(hass.config.path("blueprints/automation/ukraine_outages/notifications.yaml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target)
    calls = async_mock_service(hass, "test", "notify")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "ukraine_outages/notifications.yaml",
                    "input": {
                        "outage_entity": entity_id(hass, "binary_sensor", "outage"),
                        "next_outage_entity": entity_id(hass, "sensor", "next_outage"),
                        "schedule_entity": entity_id(hass, "event", "schedule"),
                        "lead_minutes": 15,
                        "actions": [{"action": "test.notify", "data": {"title": "{{ title }}", "message": "{{ message }}"}}],
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()
    assert calls == []

    # Tomorrow published.
    tomorrow = DAY + timedelta(days=1)
    mock_fetch.return_value = {**schedules(), tomorrow: DaySchedule(tomorrow, kyiv(9), {GROUP: (Outage(kyiv(8, day=18), kyiv(10, day=18)),)})}
    await _at(hass, freezer, kyiv(9, 6))
    assert [c.data["title"].strip() for c in calls] == ["📅 Графік на завтра"]
    assert "08:00–10:00" in calls[-1].data["message"]

    # 15 minutes before 10:00, then the outage itself, then its end.
    await _at(hass, freezer, kyiv(9, 45))
    assert calls[-1].data["title"].strip() == "⚡ Скоро відключення" and "о 10:00" in calls[-1].data["message"]
    await _at(hass, freezer, kyiv(10))
    assert calls[-1].data["title"].strip() == "🔴 Відключення за графіком"
    await _at(hass, freezer, kyiv(12))
    assert calls[-1].data["title"].strip() == "🟢 Відключення за графіком закінчилося"

    # Today's schedule changed.
    mock_fetch.return_value = {DAY: DaySchedule(DAY, kyiv(12), {GROUP: (Outage(kyiv(21), kyiv(23)),)})}
    await _at(hass, freezer, kyiv(12, 6))
    assert calls[-1].data["title"].strip() == "✏️ Графік на сьогодні змінено"
    assert "21:00–23:00" in calls[-1].data["message"] and "(було: 10:00–12:00, 21:00–22:30)" in calls[-1].data["message"]
