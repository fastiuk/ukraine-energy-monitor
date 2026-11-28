"""Real public mirror responses, trimmed to the region, freshness and twelve queue cards."""

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from html import escape

import pytest
from conftest import FakeHttp, fixture_text, kyiv, spans
from ua_outages.http import FetchError
from ua_outages.providers.base import Kind, SourceFormatError
from ua_outages.providers.vinnytsia_bezsvitla import (
    ATTRIBUTION,
    GROUPS,
    MAX_AGE,
    UNKNOWN,
    URL,
    VinnytsiaBezSvitla,
    parse,
)

DAY = date(2026, 11, 28)
TOMORROW = DAY + timedelta(days=1)
NOW = kyiv(DAY, 4)


def page():
    return fixture_text("vinnytsia_bezsvitla.html")


def intervals(items):
    """Replace only queue 1.1's JSON; the other real queues stay intact."""
    raw = items if isinstance(items, str) else json.dumps(items)
    return re.sub(
        r'data-schedule-intervals="[^"]*"',
        lambda _: 'data-schedule-intervals="' + escape(raw, quote=True) + '"',
        page(),
        count=1,
    )


def test_real_schedule_has_all_twelve_queues_and_attribution():
    schedule = parse(page(), DAY, NOW)[DAY]
    assert set(schedule.groups) == set(GROUPS) and not schedule.unknown
    assert schedule.updated_at == kyiv(DAY, 0, 30).replace(second=2)
    assert spans(schedule, "1.1") == [
        ("08:00", "10:00", False),
        ("12:00", "16:00", False),
        ("20:00", "22:00", False),
    ]
    assert spans(schedule, "5.2") == [
        ("00:00", "00:30", False),
        ("06:00", "08:00", False),
        ("12:00", "14:00", False),
        ("16:00", "20:00", False),
    ]
    assert spans(schedule, "3.1")[-1] == ("22:00", "24:00", False)
    assert schedule.notes == (ATTRIBUTION,)


@pytest.mark.parametrize(
    "bad",
    [
        "{",
        "null",
        {},
        [],
        [None],
        [{"from": "00:00", "to": "24:00", "state": "MAYBE"}],
        [{"from": "00:00", "to": "24:00", "state": []}],
        [{"from": "00:00", "to": "24:00"}],
        [{"from": "01:00", "to": "24:00", "state": "ON"}],
        [{"from": "00:00", "to": "23:00", "state": "ON"}],
        [{"from": "00:00", "to": "24:01", "state": "OFF"}],
        [{"from": "00:00", "to": "25:00", "state": "OFF"}],
        [{"from": "00:00", "to": True, "state": "OFF"}],
        [{"from": "00:00", "to": "00:00", "state": "OFF"}],
        [
            {"from": "00:00", "to": "12:00", "state": "OFF"},
            {"from": "11:00", "to": "24:00", "state": "ON"},
        ],
        [
            {"from": "00:00", "to": "12:00", "state": "OFF"},
            {"from": "13:00", "to": "24:00", "state": "ON"},
        ],
        [
            {"from": "12:00", "to": "24:00", "state": "ON"},
            {"from": "00:00", "to": "12:00", "state": "OFF"},
        ],
    ],
)
def test_incomplete_or_bad_intervals_make_only_that_queue_unknown(bad):
    schedule = parse(intervals(bad), DAY, NOW)[DAY]
    assert schedule.unknown == {"1.1": UNKNOWN}
    assert "1.1" not in schedule.groups and len(schedule.groups) == 11


def test_only_explicit_all_day_on_becomes_empty_outages():
    schedule = parse(intervals([{"from": "00:00", "to": "24:00", "state": "ON"}]), DAY, NOW)[DAY]
    assert schedule.groups["1.1"] == () and "1.1" not in schedule.unknown
    schedule = parse(
        intervals(
            [
                {"from": "00:00", "to": "12:00", "state": "OFF"},
                {"from": "12:00", "to": "24:00", "state": "OFF"},
            ]
        ),
        DAY,
        NOW,
    )[DAY]
    assert spans(schedule, "1.1") == [("00:00", "24:00", False)]


def test_missing_card_or_data_is_unknown_not_power_on():
    removed = re.sub(
        r'<article class="bz-schedule-card">.*?</article>', "", page(), count=1, flags=re.S
    )
    no_data = re.sub(r'<div class="bz-schedule-countdown"[^>]*></div>', "", page(), count=1)
    for markup in (removed, no_data):
        schedule = parse(markup, DAY, NOW)[DAY]
        assert schedule.unknown == {"1.1": UNKNOWN} and "1.1" not in schedule.groups


@pytest.mark.parametrize(
    "old,new",
    [
        ("Вінницькій", "Львівській"),
        ("на 28 листопада 2026 року", "на 27 листопада 2026 року"),
        ("/vinnytska-oblast/cherha-1-1", "/lvivska-oblast/cherha-1-1"),
        ("Черга 1.1", "Черга 7.1"),
        ('data-schedule-date="2026-11-28"', 'data-schedule-date="2026-11-27"'),
        ('data-schedule-date="2026-11-28"', 'data-schedule-date="invalid"'),
        ("2026-11-28T03:50:01+02:00", "2026-11-28T03:50:01"),
        ("2026-11-28T03:50:01+02:00", "invalid"),
        ("2026-11-28T00:30:02+02:00", "2026-11-29T00:30:02+02:00"),
    ],
)
def test_untrustworthy_page_fails(old, new):
    with pytest.raises(SourceFormatError):
        parse(page().replace(old, new, 1), DAY, NOW)


def test_duplicate_cards_and_unrecognised_page_fail():
    first = re.search(r'<article class="bz-schedule-card">.*?</article>', page(), re.S)[0]
    for markup in (
        page() + first,
        "<html>Maintenance</html>",
        re.sub(r"<article.*?</article>", "", page(), flags=re.S),
    ):
        with pytest.raises(SourceFormatError):
            parse(markup, DAY, NOW)


def test_stale_or_future_checked_stamp_fails_but_old_update_can_be_fresh():
    checked = kyiv(DAY, 3, 50).replace(second=1)
    assert parse(page(), DAY, checked + MAX_AGE)[DAY].groups
    with pytest.raises(FetchError):
        parse(page(), DAY, checked + MAX_AGE + timedelta(seconds=1))
    with pytest.raises(FetchError):
        parse(page(), DAY, checked - timedelta(minutes=6))
    # A schedule can stay unchanged while the mirror keeps checking its source.
    old_update = page().replace("2026-11-28T00:30:02+02:00", "2026-11-27T19:55:00+02:00")
    assert parse(old_update, DAY, NOW)[DAY].updated_at.date() == DAY - timedelta(days=1)


def test_explicit_unpublished_day_is_absent_and_not_assumed_all_on():
    empty = fixture_text("vinnytsia_bezsvitla_unpublished.html")
    assert parse(empty, TOMORROW, NOW) == {}
    for markup in (
        empty.replace("29.11.2026", "28.11.2026"),
        empty.replace("ще не опублікований", "недоступний"),
        empty + page(),
    ):
        with pytest.raises(SourceFormatError):
            parse(markup, TOMORROW, NOW)


def test_fetch_uses_two_exact_kyiv_dates_and_source_is_a_mirror():
    source = VinnytsiaBezSvitla()
    http = FakeHttp(
        {
            "/grafik-na-2026-11-28": page(),
            "/grafik-na-2026-11-29": fixture_text("vinnytsia_bezsvitla_unpublished.html"),
        }
    )
    result = asyncio.run(source.fetch(http, datetime(2026, 11, 28, 2, tzinfo=UTC)))
    assert list(result) == [DAY]
    assert [c.url for c in http.calls] == [
        f"{URL}/grafik-na-2026-11-28",
        f"{URL}/grafik-na-2026-11-29",
    ]
    assert all(c.method == "GET" and not c.params for c in http.calls)
    assert source.kind == Kind.MIRROR and not source.official and source.link == URL
    assert source.hosts == {"bezsvitla.com.ua"} and source.min_interval == 600


def test_fetch_tomorrow_failure_does_not_silently_return_partial_refresh():
    with pytest.raises(FetchError):
        asyncio.run(VinnytsiaBezSvitla().fetch(FakeHttp({"/grafik-na-2026-11-28": page()}), NOW))


def test_fetch_keeps_published_tomorrow_under_its_own_date():
    tomorrow = (
        page()
        .replace('data-schedule-date="2026-11-28"', 'data-schedule-date="2026-11-29"')
        .replace("на 28 листопада 2026 року", "на 29 листопада 2026 року")
    )
    http = FakeHttp({"/grafik-na-2026-11-28": page(), "/grafik-na-2026-11-29": tomorrow})
    schedules = asyncio.run(VinnytsiaBezSvitla().fetch(http, NOW))
    assert list(schedules) == [DAY, TOMORROW]
    assert schedules[TOMORROW].groups["1.1"][0].start.date() == TOMORROW
