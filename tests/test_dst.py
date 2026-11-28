from datetime import UTC, date, datetime, timedelta

import pytest

from ua_outages.models import (
    KYIV,
    DaySchedule,
    Outage,
    Slot,
    at,
    merge_outages,
    outages_from_slots,
)
from ua_outages.timeline import (
    combine_schedules,
    current_outage,
    diff_schedules,
    next_outage,
)

FALL = date(2026, 10, 25)
SPRING = date(2026, 3, 29)


def repeated(hour: int, minute: int = 0, *, fold: int = 0) -> datetime:
    return datetime(2026, 10, 25, hour, minute, tzinfo=KYIV, fold=fold)


def instants(outages):
    return [(o.start.astimezone(UTC), o.end.astimezone(UTC), o.possible) for o in outages]


@pytest.mark.parametrize("day,hours", [(SPRING, 23), (FALL, 25)])
def test_duration_counts_elapsed_hours_on_clock_change_days(day, hours):
    assert Outage(at(day, 0), at(day, 24)).duration == timedelta(hours=hours)


def test_outage_can_end_at_an_earlier_wall_time_after_clocks_go_back():
    outage = Outage(repeated(3, 45), repeated(3, 15, fold=1))
    assert outage.duration == timedelta(minutes=30)


@pytest.mark.parametrize("start,end", [(3, 5), (2, 3)])
def test_nonexistent_interval_endpoints_are_rejected(start, end):
    with pytest.raises(ValueError, match="nonexistent outage endpoint"):
        Outage(at(SPRING, start), at(SPRING, end))


@pytest.mark.parametrize("indices", [[6], [7], [6, 7]])
def test_slots_in_skipped_hour_do_not_become_phantom_or_negative_outages(indices):
    slots = [Slot.OFF if i in indices else Slot.ON for i in range(48)]
    with pytest.raises(ValueError, match="nonexistent outage endpoint"):
        outages_from_slots(SPRING, slots)


@pytest.mark.parametrize("slot", [Slot.OFF, Slot.MAYBE])
def test_equal_slots_across_spring_change_are_joined_before_endpoint_validation(slot):
    all_day = outages_from_slots(SPRING, [slot] * 48)
    assert len(all_day) == 1
    assert all_day[0].duration == timedelta(hours=23)
    assert all_day[0].possible is (slot is Slot.MAYBE)

    slots = [Slot.ON] * 48
    slots[4:10] = [slot] * 6  # 02:00–05:00 spans the nonexistent 03:00 hour.
    crossing = outages_from_slots(SPRING, slots)
    assert len(crossing) == 1
    assert crossing[0].start == at(SPRING, 2)
    assert crossing[0].end == at(SPRING, 5)
    assert crossing[0].duration == timedelta(hours=2)


def test_outage_equality_order_and_hash_distinguish_repeated_hour():
    first = Outage(repeated(3, 10), repeated(3, 20))
    second = Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1))
    assert first != second
    assert len({first, second}) == 2
    assert first < second
    assert first <= second
    assert second > first
    assert second >= first
    assert not (first > second or first >= second)
    assert first == Outage(first.start.astimezone(UTC), first.end.astimezone(UTC))
    assert hash(first) == hash(Outage(first.start.astimezone(UTC), first.end.astimezone(UTC)))
    assert first != object()
    with pytest.raises(TypeError):
        assert first < object()


def test_merge_keeps_separate_outages_with_the_same_wall_times():
    first = Outage(repeated(3, 10), repeated(3, 20))
    second = Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1))
    assert instants(merge_outages([second, first])) == instants([first, second])


def test_merge_sorts_and_joins_intervals_across_the_repeated_hour():
    crossing = Outage(repeated(3, 45), repeated(3, 15, fold=1))
    later = Outage(repeated(3, 5, fold=1), repeated(3, 30, fold=1))
    expected = Outage(crossing.start, later.end)
    assert instants(merge_outages([later, crossing])) == instants([expected])


def test_definite_intervals_cut_possible_outage_in_both_occurrences():
    possible = Outage(repeated(3, 10), repeated(3, 50, fold=1), True)
    first = Outage(repeated(3, 20), repeated(3, 30))
    second = Outage(repeated(3, 20, fold=1), repeated(3, 30, fold=1))
    expected = [
        Outage(possible.start, first.start, True),
        first,
        Outage(first.end, second.start, True),
        second,
        Outage(second.end, possible.end, True),
    ]
    assert instants(merge_outages([second, possible, first])) == instants(expected)


def test_current_outage_checks_the_actual_occurrence_of_repeated_hour():
    outage = Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1))
    schedule = DaySchedule(FALL, groups={"1.1": (outage,)})
    assert current_outage([schedule], "1.1", repeated(3, 15)) is None
    assert current_outage([schedule], "1.1", repeated(3, 15, fold=1)) == outage
    assert current_outage([schedule], "1.1", outage.end) is None


def test_current_outage_spans_the_clock_change():
    outage = Outage(repeated(3, 45), repeated(3, 15, fold=1))
    schedule = DaySchedule(FALL, groups={"1.1": (outage,)})
    assert current_outage([schedule], "1.1", repeated(3, 50)) == outage
    assert current_outage([schedule], "1.1", repeated(3, 5, fold=1)) == outage
    assert current_outage([schedule], "1.1", repeated(3, 30)) is None
    assert current_outage([schedule], "1.1", repeated(3, 20, fold=1)) is None


def test_next_outage_can_have_an_earlier_wall_time():
    outage = Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1))
    schedule = DaySchedule(FALL, groups={"1.1": (outage,)})
    assert next_outage([schedule], "1.1", repeated(3, 45)) == outage
    assert next_outage([schedule], "1.1", outage.start) is None


def test_diff_detects_a_change_to_the_other_occurrence_of_repeated_hour():
    first = Outage(repeated(3, 10), repeated(3, 20))
    second = Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1))
    old = {FALL: DaySchedule(FALL, groups={"1.1": (first,)})}
    new = {FALL: DaySchedule(FALL, groups={"1.1": (second,)})}
    changes = diff_schedules(old, new)
    assert len(changes) == 1
    assert changes[0].groups == ("1.1",)


def test_serialization_preserves_both_occurrences():
    outages = (
        Outage(repeated(3, 10), repeated(3, 20)),
        Outage(repeated(3, 10, fold=1), repeated(3, 20, fold=1)),
    )
    schedule = DaySchedule(FALL, groups={"1.1": outages})
    restored = DaySchedule.from_dict(schedule.to_dict())
    assert restored == schedule
    assert instants(restored.outages("1.1")) == instants(outages)
    assert restored.outages("1.1")[0].start.fold == 0
    assert restored.outages("1.1")[1].start.fold == 1


def test_combine_uses_latest_update_in_absolute_time():
    earlier = DaySchedule(FALL, repeated(3, 45), {"1.1": ()})
    later = DaySchedule(FALL, repeated(3, 15, fold=1), {"1.2": ()})
    combined = combine_schedules([{FALL: earlier}, {FALL: later}])[FALL]
    assert combined.updated_at.astimezone(UTC) == later.updated_at.astimezone(UTC)
