from datetime import date

import pytest

from conftest import fixture_json, spans
from ua_outages.providers import mykolaiv
from ua_outages.providers.base import SourceFormatError

DAY = date(2026, 10, 17)


def parse_cells(cells):
    active = fixture_json("mykolaiv_active.json")
    active[0]["series"] = cells
    return mykolaiv.parse(
        fixture_json("mykolaiv_queues.json"), fixture_json("mykolaiv_series.json"), active
    )[DAY]


@pytest.mark.parametrize("kind", ["UNKNOWN", "", None, [], {}])
def test_unknown_cell_type_is_not_power_on(kind):
    with pytest.raises(SourceFormatError):
        parse_cells([{"time_series_id": 22, "outage_queue_id": 14, "type": kind}])


def test_missing_cell_type_is_not_power_on():
    with pytest.raises(SourceFormatError):
        parse_cells([{"time_series_id": 22, "outage_queue_id": 14}])


@pytest.mark.parametrize("key", ["time_series_id", "outage_queue_id"])
@pytest.mark.parametrize("value", [None, 9999, [], {}, True])
def test_invalid_reference_is_not_power_on(key, value):
    cell = {"time_series_id": 22, "outage_queue_id": 14, "type": "OFF"}
    cell[key] = value
    with pytest.raises(SourceFormatError):
        parse_cells([cell])


@pytest.mark.parametrize("key", ["time_series_id", "outage_queue_id"])
def test_missing_reference_is_not_power_on(key):
    cell = {"time_series_id": 22, "outage_queue_id": 14, "type": "OFF"}
    del cell[key]
    with pytest.raises(SourceFormatError):
        parse_cells([cell])


@pytest.mark.parametrize("cell", [None, [], "OFF"])
def test_malformed_cell_is_not_power_on(cell):
    with pytest.raises(SourceFormatError):
        parse_cells([cell])


def test_invalid_cell_does_not_leave_a_partial_schedule():
    with pytest.raises(SourceFormatError):
        parse_cells([
            {"time_series_id": 22, "outage_queue_id": 14, "type": "OFF"},
            {"time_series_id": 23, "outage_queue_id": 14, "type": "UNKNOWN"},
        ])


@pytest.mark.parametrize("kind, possible", [("OFF", False), ("SURE_OFF", False), ("PROBABLY_OFF", True)])
def test_supported_outage_types(kind, possible):
    schedule = parse_cells([{"time_series_id": 22, "outage_queue_id": 14, "type": kind}])
    assert spans(schedule, "1.1") == [("10:30", "11:00", possible)]
    assert schedule.groups["1.2"] == ()


@pytest.mark.parametrize("cells", [[], [{"time_series_id": 22, "outage_queue_id": 14, "type": "ENABLE"}]])
def test_published_empty_schedule_remains_power_on(cells):
    schedule = parse_cells(cells)
    assert schedule.groups
    assert all(outages == () for outages in schedule.groups.values())
    assert not schedule.unknown


def test_malformed_active_entry_is_not_unpublished():
    with pytest.raises(SourceFormatError):
        mykolaiv.parse(
            fixture_json("mykolaiv_queues.json"), fixture_json("mykolaiv_series.json"), [None]
        )
