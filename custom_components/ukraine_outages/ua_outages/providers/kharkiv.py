"""Kharkivoblenergo: the schedule as text, from the official Telegram channel and the site's RSS feed.

Both carry the same text, posted the evening before::

    ‼️ За вказівкою НЕК "Укренерго" … у п'ятницю, 20 листопада, з 00:00 до 24:00 у Харківській
    області будуть діяти графіки погодинних відключень (ГПВ).
    1.1 00:00-03:00; 10:00-13:30; 17:00-24:00
    …
    ➡️ ДЛЯ ПРОМИСЛОВОСТІ ТА БІЗНЕСУ … графіки обмеження потужності (ГОП).

- The channel's public preview ``https://t.me/s/kharkivenergy`` shows its latest ~20 posts.
- ``GET https://oblenergo.kharkiv.ua/uk/rss.xml``: items titled "У Харківській області
  17 жовтня діятимуть графіки погодинних відключень", with the text in the description. Since
  16.11.2026 the whole site answers mostly with a Cloudflare challenge, which is not worked
  around: the feed is the fallback.

The day is the one in the line announcing the schedule; the newest post per day wins. "… не
застосовуватимуться" there means no outages that day. A group whose line holds a time that is
not part of a span ("17:00:20:30" is read as 17:00–20:30, but "17:00 20:30" is not) is left
unrecognised, with its text, rather than read in part.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import KYIV, DaySchedule
from ._html import Node, parse_html
from ._text import find_day, html_lines, parse_group_text, unescape
from .base import Kind, Provider, fail

URL = "https://oblenergo.kharkiv.ua/uk/rss.xml"
CHANNEL = "kharkivenergy"
CHANNEL_URL = f"https://t.me/s/{CHANNEL}"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
MAX_ITEM = 200_000
MAX_POST = 5000
_SCHEDULE_RE = re.compile(r"графік\w{0,2}\s{1,3}погодинних\s{1,3}відключень", re.IGNORECASE)
_NOT_APPLIED_RE = re.compile(r"не\s{1,3}(?:застосов|діят|плану|будуть)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"[0-9]{1,12}")


def parse_text(announcement: str, text: str, published: datetime | None, today: date) -> DaySchedule | None:
    """The schedule announced by ``announcement`` with its groups in ``text``; None for anything else."""
    if not _SCHEDULE_RE.search(announcement) or "ГОП" in announcement:
        return None
    day = find_day(announcement, near=published.date() if published else today)
    if day is None or day < today:
        return None
    groups, unknown = parse_group_text(day, text, strict=True)
    if not groups and not unknown:
        if not _NOT_APPLIED_RE.search(announcement):
            return None
        groups = dict.fromkeys(GROUPS, ())
    return DaySchedule(day, published, groups, unknown)


def _newest(found: dict[date, DaySchedule], schedule: DaySchedule | None) -> None:
    if schedule is None:
        return
    current = found.get(schedule.day)
    if current is None or (schedule.updated_at and (current.updated_at is None or schedule.updated_at > current.updated_at)):
        found[schedule.day] = schedule


# The RSS feed


def _elements(text: str, tag: str, limit: int) -> list[str]:
    """Contents of every ``<tag>…</tag>``, found in one pass with ``str.find`` (no regex retries)."""
    found, start, opening, closing = [], 0, f"<{tag}>", f"</{tag}>"
    while (begin := text.find(opening, start)) >= 0:
        begin += len(opening)
        if (end := text.find(closing, begin)) < 0:
            break
        if end - begin <= limit:
            found.append(text[begin:end])
        start = end + len(closing)
    return found


def _first(text: str, tag: str, limit: int) -> str:
    return next(iter(_elements(text, tag, limit)), "")


def parse_feed(feed: str, today: date) -> dict[date, DaySchedule]:
    if "<rss" not in feed[:1000]:
        fail("Kharkiv: not an RSS feed")
    best: dict[date, DaySchedule] = {}
    for item in _elements(feed, "item", MAX_ITEM):
        title = unescape(_first(item, "title", 1000).strip())
        try:
            stamp = _first(item, "pubDate", 100).strip()
            published = parsedate_to_datetime(stamp).astimezone(KYIV) if stamp else None
        except (TypeError, ValueError, OverflowError):
            published = None
        body = "\n".join(html_lines(unescape(_first(item, "description", MAX_ITEM))))
        _newest(best, parse_text(title, body, published, today))
    return dict(sorted(best.items()))


class Kharkiv(Provider):
    hosts = frozenset({"oblenergo.kharkiv.ua"})
    ukraine_only = True  # times out or answers 403 from GitHub's runners
    min_interval = 300
    kind = Kind.PAGE
    page = URL

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL, headers={"Accept": "application/rss+xml, application/xml"})
        return await asyncio.to_thread(parse_feed, resp.text(), now.astimezone(KYIV).date())


# The Telegram channel


def _lines(node: Node) -> list[str]:
    """The text of a post, one line per ``<br>``."""
    parts: list[str] = []
    stack: list[Node | str] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif item.tag == "br":
            parts.append("\n")
        else:
            stack.extend(reversed(item.children))
    return [" ".join(line.split()) for line in "".join(parts)[:MAX_POST].split("\n")]


def parse_channel(markup: str, today: date) -> dict[date, DaySchedule]:
    root = parse_html(markup)
    if root.find(cls="tgme_channel_history") is None:
        fail("Kharkiv: not a Telegram channel preview")
    best: dict[date, DaySchedule] = {}
    for post in root.find_all("div", cls="tgme_widget_message"):
        number = post.attrs.get("data-post", "").rpartition("/")[2]
        body, moment = post.find("div", cls="tgme_widget_message_text"), post.find("time")
        if not _NUMBER_RE.fullmatch(number) or body is None or moment is None:
            continue
        try:
            published = datetime.fromisoformat(moment.attrs.get("datetime", "")).astimezone(KYIV)
        except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
            continue
        lines = _lines(body)
        announcement = next((line for line in lines if _SCHEDULE_RE.search(line)), "")
        _newest(best, parse_text(announcement, "\n".join(lines), published, today))
    return dict(sorted(best.items()))


class KharkivTelegram(Provider):
    hosts = frozenset({"t.me"})
    min_interval = 300
    kind = Kind.TELEGRAM
    page = CHANNEL_URL

    @property
    def name(self) -> str:
        return f"Telegram {CHANNEL}"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        schedules = await asyncio.to_thread(parse_channel, (await http.get(CHANNEL_URL)).text(), today)
        return {day: s for day, s in schedules.items() if day <= today + timedelta(days=1)}
