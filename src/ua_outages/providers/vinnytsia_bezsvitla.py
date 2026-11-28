"""Vinnytsia's public queue schedules on the independent BezSvitla mirror.

The date-specific region pages contain all twelve queues as HTML cards. Each card's
``data-schedule-intervals`` is JSON covering the entire day with ``ON`` / ``OFF`` spans;
``data-schedule-date`` identifies that day. No address, cookies or private API is used.
The mirror's checked timestamp must be recent; its updated timestamp says when the
schedule last changed, not when it was checked. An explicitly unpublished day is absent,
never an all-day power-on schedule. The source link travels with the schedule as attribution.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta

from ..http import Fetcher, FetchError
from ..models import KYIV, DaySchedule, Outage, at_minute, merge_outages
from ._html import Node, parse_html
from ._text import MONTHS
from .base import Kind, Provider, fail

URL = "https://bezsvitla.com.ua/vinnytska-oblast"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
MAX_AGE = timedelta(hours=3)
CLOCK_SKEW = timedelta(minutes=5)
UNKNOWN = "Нерозпізнаний графік"
ATTRIBUTION = f"Неофіційне джерело: {URL}"
_HEADING = "Графік відключень світла у Вінницькій області "
_GROUP_RE = re.compile(r"Черга ([1-6])\.([12])")
_TIME_RE = re.compile(r"([0-2][0-9]):([0-5][0-9])")


def _stamp(root: Node, suffix: str) -> datetime:
    holder = root.find("span", cls=f"bz-schedule-hero__stamp--{suffix}")
    node = holder.find("time") if holder else None
    raw = node.attrs.get("datetime", "") if node else ""
    try:
        stamp = datetime.fromisoformat(raw) if len(raw) <= 40 else None
        if stamp is not None and stamp.tzinfo is not None:
            return stamp.astimezone(KYIV)
    except (ValueError, OverflowError):
        pass
    fail(f"BezSvitla Vinnytsia: missing or invalid {suffix} timestamp")


def _minute(value: object) -> int | None:
    if not isinstance(value, str) or (m := _TIME_RE.fullmatch(value)) is None:
        return None
    hour, minute = int(m[1]), int(m[2])
    return 60 * hour + minute if hour < 24 or (hour, minute) == (24, 0) else None


def _intervals(raw: str, day: date) -> tuple[Outage, ...] | None:
    """Require ordered, contiguous coverage from 00:00 to 24:00, including ON spans."""
    if len(raw) > 100_000:
        return None
    try:
        items = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(items, list) or not 1 <= len(items) <= 1440:
        return None
    end, outages = 0, []
    for item in items:
        if not isinstance(item, dict) or item.get("state") not in ("ON", "OFF"):
            return None
        start, stop = _minute(item.get("from")), _minute(item.get("to"))
        if start != end or stop is None or stop <= start:
            return None
        if item["state"] == "OFF":
            outages.append(Outage(at_minute(day, start), at_minute(day, stop)))
        end = stop
    return merge_outages(outages) if end == 1440 else None


def parse(markup: str, day: date, now: datetime) -> dict[date, DaySchedule]:
    """Read exactly the requested day, rejecting stale data and another region's page."""
    root = parse_html(markup)
    heading = root.find("h1", cls="bz-schedule-hero__title")
    month = next(name for name, number in MONTHS.items() if number == day.month)
    expected_heading = f"{_HEADING}на {day.day} {month} {day.year} року"
    if heading is None or heading.text(200) != expected_heading:
        fail("BezSvitla Vinnytsia: not the region's schedule page for the requested date")
    cards = root.find_all("article", cls="bz-schedule-card")
    empty = root.find("div", cls="bz-schedule-empty")
    if empty is not None:
        message = empty.find("strong")
        expected = f"Графік відключень на {day:%d.%m.%Y} ще не опублікований."
        if cards or message is None or message.text(200) != expected:
            fail("BezSvitla Vinnytsia: unexpected unpublished-day message")
        return {}
    if not cards or len(cards) > len(GROUPS):
        fail("BezSvitla Vinnytsia: missing or unexpected schedule cards")

    checked, updated = _stamp(root, "checked"), _stamp(root, "updated")
    age = now.astimezone(UTC) - checked.astimezone(UTC)
    if age > MAX_AGE or age < -CLOCK_SKEW:
        raise FetchError(
            f"BezSvitla Vinnytsia: the mirror was last checked at {checked.isoformat()}"
        )
    if updated.astimezone(UTC) > min(checked.astimezone(UTC), now.astimezone(UTC)) + CLOCK_SKEW:
        fail("BezSvitla Vinnytsia: schedule update is in the future")

    groups: dict[str, tuple[Outage, ...]] = {}
    unknown = dict.fromkeys(GROUPS, UNKNOWN)
    seen: set[str] = set()
    for card in cards:
        label = card.find("span", cls="bz-schedule-card__heading")
        title = label.find("strong") if label else None
        match = _GROUP_RE.fullmatch(title.text(40)) if title else None
        if match is None:
            fail("BezSvitla Vinnytsia: unrecognised queue heading")
        group = f"{match[1]}.{match[2]}"
        link = card.find("a", cls="bz-schedule-card__head-link")
        if (
            link is None
            or link.attrs.get("href") != f"/vinnytska-oblast/cherha-{match[1]}-{match[2]}"
        ):
            fail("BezSvitla Vinnytsia: queue link belongs to another region or queue")
        if group in seen:
            fail("BezSvitla Vinnytsia: duplicate queue")
        seen.add(group)
        data = card.find_all("div", cls="bz-schedule-countdown")
        if len(data) != 1:
            continue
        if data[0].attrs.get("data-schedule-date") != day.isoformat():
            fail("BezSvitla Vinnytsia: schedule date differs from the requested day")
        outages = _intervals(data[0].attrs.get("data-schedule-intervals", ""), day)
        if outages is not None:
            groups[group] = outages
            unknown.pop(group)
    return {day: DaySchedule(day, updated, groups, unknown, notes=(ATTRIBUTION,))}


class VinnytsiaBezSvitla(Provider):
    hosts = frozenset({"bezsvitla.com.ua"})
    kind = Kind.MIRROR
    page = URL
    min_interval = 600

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        schedules: dict[date, DaySchedule] = {}
        # Exact dates avoid interpreting yesterday's cached "today" page after midnight.
        # Keep requests sequential and let either failure fail this refresh as a whole.
        for day in (today, today + timedelta(days=1)):
            response = await http.get(f"{URL}/grafik-na-{day.isoformat()}")
            schedules.update(await asyncio.to_thread(parse, response.text(), day, now))
        return schedules
