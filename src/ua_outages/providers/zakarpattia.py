"""Zakarpattiaoblenergo: two schedule images, today's and tomorrow's, on the inneti platform (as Volyn).

::

    GET https://api-outage-zakarpat-energy.inneti.net/api/options?option_key[]=pw_gpv_image_today&option_key[]=pw_gpv_image_tomorrow
        → {"hydra:member": [{"option_key": "pw_gpv_image_today", "value": "/media/2026/11/6b000475a4f3e_GPV.png",
                             "date": "2026-11-20T10:10:33+00:00"}, ...]}

The operator's page shows both images, ``""`` meaning none (as from 01.10 to 19.11.2026, while
the schedules were not applied). ``date`` is when the option was set: Kyiv wall-clock time
labelled UTC, as everywhere on the platform. There is no archive here to tell an image's day,
and which slot holds which day is up to whoever uploads (on Volyn, the same platform, "today"
gets the next day's image late in the evening), so the day is read from the image itself.

The image (2020×530 since 20.11.2026, shown at half size on the page) is one table:

- a title «Прогнозовані години включення/відключення електроенергії на 20.11.2026р.»;
- a header with the times «00:00», «00:30», … rotated, bold on the hour;
- «Черга», «Підчерга» and 24 hour columns; 12 subqueue rows 1-1…6-2 in six bordered blocks.

Black lines separate the hours; a half hour is not lined, but filled from the hour's start or
its middle. A subqueue's off cells have the colour of its own «Підчерга» cell (each queue has
its own: yellow, green, orange, …), on cells are white. Any other colour leaves the subqueue
unrecognised instead of guessed.

The date is read without an OCR engine: the header's bold «HH:00» labels hold every digit,
drawn by the same renderer at the size of the title's, so they are the templates the title's
«DD.MM.YYYY» is matched against, at the best sub-pixel alignment. A digit that matches no
template closely, or two alike, fails the image loudly: a wrong day is worse than none.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from itertools import combinations, pairwise
from typing import Any, NamedTuple

from PIL import Image, ImageChops, ImageStat

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._image import decode_png, runs
from .base import Kind, Provider, fail, url_on_host

log = logging.getLogger(__name__)

BASE_URL = "https://api-outage-zakarpat-energy.inneti.net"
OPTION_KEYS = ("pw_gpv_image_today", "pw_gpv_image_tomorrow")
OPTIONS_URL = f"{BASE_URL}/api/options?" + "&".join(f"option_key[]={key}" for key in OPTION_KEYS)
HEADERS = {"Accept": "application/ld+json"}
MAX_MEMBERS = 100

GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
HOURS = 24
#: 2020×530 since 20.11.2026; the table is found from its lines, so a resized one is let through.
SIZE = (range(1000, 3001), range(260, 1001))
#: Luminance below this is a grid line (black); the queue colours are all far lighter.
LINE_LEVEL = 64
#: Luminance below this is the ink of text.
INK_LEVEL = 160
WHITE = (255, 255, 255)
COLOR_TOLERANCE = 24
#: Spans between lines narrower than this are gaps between double borders, not cells.
MIN_CELL = 8
#: Pixels trimmed from each side of a half-hour cell before reading its colour.
INSET = 3
#: Sub-pixel steps per pixel when aligning a glyph with a template.
UP = 3
#: A digit matches a template when under this share of their ink differs (the same digit:
#: 0.00–0.05 on real images; different digits: 0.12 and more) …
MATCH = 0.08
#: … and every other digit's templates differ by this much more.
MARGIN = 0.05
TEMPLATES_PER_DIGIT = 4
#: An image's day further ahead than this is a misreading, not a schedule.
MAX_AHEAD = timedelta(days=7)
UNRECOGNISED = "Нерозпізнаний графік"

Span = tuple[int, int]  # [start, end) in pixels
RGB = tuple[int, int, int]
Decoded = tuple[dict[str, tuple[Outage, ...]], dict[str, str]]  # outages and unrecognised groups


class Shown(NamedTuple):
    path: str
    set_at: datetime | None


class Grid(NamedTuple):
    title: Span  # rows of the title
    header: Span  # rows of the time labels
    label: Span  # columns of the «Підчерга» cells
    hours: list[Span]  # columns of the hours
    rows: list[Span]  # rows of the subqueues, 1.1 to 6.2
    colours: list[RGB]  # each subqueue's colour, which its off cells have


class Glyph(NamedTuple):
    width: int
    height: int
    pixels: Image.Image  # greyscale, cropped to the ink


def _members(payload: Any) -> list[Any]:
    # A JSON-LD collection, or the plain list a cache in front of the API may serve instead.
    members = payload.get("hydra:member") if isinstance(payload, dict) else payload
    if not isinstance(members, list) or len(members) > MAX_MEMBERS:
        fail("Zakarpattia: options response has no list of options")
    return members


def _set_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Kyiv wall-clock time labelled UTC: "10:10:33+00:00" was set at 10:10 in Kyiv.
    return moment.replace(tzinfo=KYIV)


def parse_options(payload: Any) -> list[Shown]:
    """The images on show, today's slot first; empty while there are none."""
    options = {
        m["option_key"]: m for m in _members(payload) if isinstance(m, dict) and m.get("option_key") in OPTION_KEYS
    }
    if not options:
        fail("Zakarpattia: no schedule image options")
    shown = []
    for key in OPTION_KEYS:
        option = options.get(key, {})
        value = option.get("value")
        if value in (None, ""):
            continue
        if url_on_host(BASE_URL, value) is None:
            fail(f"Zakarpattia: {key} is not a plain path: {str(value)[:80]!r}")
        shown.append(Shown(value, _set_at(option.get("date"))))
    return shown


# The table


def _near(color: Sequence[int], reference: Sequence[int]) -> bool:
    return all(abs(a - b) <= COLOR_TOLERANCE for a, b in zip(color, reference, strict=True))


def _cells(lines: Sequence[Span]) -> list[Span]:
    """The spans between consecutive lines, leaving out the gaps between double borders."""
    return [(a[1], b[0]) for a, b in pairwise(lines) if b[0] - a[1] >= MIN_CELL]


def _even(spans: Sequence[Span], what: str, smallest: int) -> None:
    sizes = [end - start for start, end in spans]
    if min(sizes) < smallest or max(sizes) - min(sizes) > max(3, min(sizes) // 10):
        fail(f"Zakarpattia: uneven {what} in the schedule table")


def _fill(image: Image.Image, columns: Span, rows: Span, share: float = 0.9) -> RGB | None:
    """The colour filling ``share`` of a cell or more, or None if no one colour does."""
    box = (columns[0] + INSET, rows[0] + INSET, columns[1] - INSET, rows[1] - INSET)
    area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    colors = image.crop(box).getcolors(area)  # anti-aliased text has many
    if not colors:
        return None
    count, color = max(colors)
    return color if count >= share * area else None


def locate_grid(image: Image.Image) -> Grid:
    width, height = image.size
    line = image.convert("L").point(lambda v: 1 if v < LINE_LEVEL else 0).tobytes()

    # Columns: the hour lines run down the whole table, the half-hour lines only through the
    # header, and no fill is dark enough to count.
    counts = [sum(line[x::width]) for x in range(width)]
    columns = _cells(runs([count > 0.6 * height for count in counts]))
    if len(columns) != HOURS + 2:  # "Черга", "Підчерга", then the hours
        fail(f"Zakarpattia: expected {HOURS + 2} table columns, found {len(columns)}")
    label, hours = columns[1], columns[2:]
    _even(hours, "hour columns", 4 * INSET + 8)  # two half-hour cells and their insets

    # Rows: lines across the hours. The subqueue rows are those with a coloured label cell;
    # the title and the header above them are white.
    left, right = hours[0][0], hours[-1][1]
    counts = [sum(line[y * width + left : y * width + right]) for y in range(height)]
    bands = _cells(runs([count > 0.6 * (right - left) for count in counts]))
    # A label cell holds its name ("1-1") too.
    found = [(band, c) for band in bands if (c := _fill(image, label, band, share=0.6)) and not _near(c, WHITE)]
    if len(found) != len(GROUPS):
        fail(f"Zakarpattia: expected {len(GROUPS)} subqueue rows, found {len(found)}")
    rows, colours = [band for band, _ in found], [colour for _, colour in found]
    _even(rows, "subqueue rows", 2 * INSET + 4)
    queues = colours[::2]
    if any(not _near(a, b) for a, b in zip(colours[::2], colours[1::2], strict=True)) or any(
        _near(a, b) for a, b in combinations(queues, 2)
    ):
        fail("Zakarpattia: the subqueue colours are not one per queue")
    first = bands.index(rows[0])
    if first < 2 or bands[first : first + len(rows)] != rows:
        fail("Zakarpattia: no title and header above the subqueues")
    return Grid(bands[first - 2], bands[first - 1], label, hours, rows, colours)


def _slot(image: Image.Image, columns: Span, row: Span, colour: RGB) -> Slot | None:
    fill = _fill(image, columns, row)
    if fill is None:
        return None
    if _near(fill, WHITE):
        return Slot.ON
    if _near(fill, colour):
        return Slot.OFF
    return None


def _halves(span: Span) -> tuple[Span, Span]:
    middle = (span[0] + span[1]) // 2
    return (span[0], middle), (middle, span[1])


# The date


def _glyphs(strip: Image.Image) -> list[Glyph]:
    """Each run of inked columns, left to right, cropped to its ink."""
    width, height = strip.size
    ink = strip.point(lambda v: 1 if v < INK_LEVEL else 0).tobytes()
    glyphs = []
    for x0, x1 in runs([any(ink[x::width]) for x in range(width)]):
        inked = [y for y in range(height) if any(ink[y * width + x0 : y * width + x1])]
        top, bottom = inked[0], inked[-1] + 1
        glyphs.append(Glyph(x1 - x0, bottom - top, strip.crop((x0, top, x1, bottom))))
    return glyphs


def _split(glyph: Glyph, digit_width: int) -> list[Glyph]:
    """Two touching digits ("11" in bold) as two glyphs, cut at the column with the least ink."""
    if not 1.7 * digit_width <= glyph.width <= 2.4 * digit_width:
        return [glyph]
    ink = glyph.pixels.point(lambda v: 1 if v < INK_LEVEL else 0).tobytes()
    third = glyph.width // 3
    cut = min(range(third, glyph.width - third), key=lambda x: sum(ink[x :: glyph.width]))
    return [
        Glyph(cut, glyph.height, glyph.pixels.crop((0, 0, cut, glyph.height))),
        Glyph(glyph.width - cut, glyph.height, glyph.pixels.crop((cut, 0, glyph.width, glyph.height))),
    ]


def _prepared(glyph: Glyph) -> Image.Image:
    """Ink as light on black, scaled up for sub-pixel alignment."""
    padded = Image.new("L", (glyph.width + 2, glyph.height + 2), 255)
    padded.paste(glyph.pixels, (1, 1))
    return ImageChops.invert(padded.resize((padded.width * UP, padded.height * UP), Image.Resampling.BILINEAR))


def _distance(a: Image.Image, b: Image.Image) -> float:
    """The share of two glyphs' ink that differs at their best alignment, within a pixel."""
    if abs(a.width - b.width) > UP or abs(a.height - b.height) > UP:
        return 1.0
    size = (max(a.width, b.width) + 2 * UP, max(a.height, b.height) + 2 * UP)
    base = Image.new("L", size)
    base.paste(a, (UP, UP))
    ink = sum(ImageStat.Stat(a).sum) + sum(ImageStat.Stat(b).sum)
    best = 1.0
    for dx in range(-UP, UP + 1):
        for dy in range(-UP, UP + 1):
            other = Image.new("L", size)
            other.paste(b, (UP + dx, UP + dy))
            best = min(best, sum(ImageStat.Stat(ImageChops.difference(base, other)).sum) / max(ink, 1))
    return best


def _templates(grey: Image.Image, grid: Grid) -> tuple[dict[str, list[Image.Image]], Glyph]:
    """Each digit as the header's bold «HH:00» labels draw it, and one of them for its size."""
    templates: dict[str, list[Image.Image]] = {}
    sample = None
    for hour, column in enumerate(grid.hours):
        on_the_hour, _ = _halves(column)
        cell = grey.crop((on_the_hour[0] + 1, grid.header[0] + 1, on_the_hour[1] - 1, grid.header[1] - 1))
        glyphs = _glyphs(cell.rotate(-90, expand=True))  # the labels read upwards
        if len(glyphs) < 4:
            continue
        sample = sample or glyphs[-1]  # the last "0" of "HH:00"
        glyphs = [part for glyph in glyphs for part in _split(glyph, sample.width)]
        if len(glyphs) != 5 or glyphs[2].height > sample.height * 0.9:  # "HH:00", the colon short
            continue
        for character, glyph in zip(f"{hour:02d}:00", glyphs, strict=True):
            if character != ":" and len(found := templates.setdefault(character, [])) < TEMPLATES_PER_DIGIT:
                found.append(_prepared(glyph))
    if sample is None or len(templates) != 10:
        fail("Zakarpattia: the header's time labels do not show every digit")
    return templates, sample


def _digit(glyph: Glyph, templates: dict[str, list[Image.Image]]) -> str | None:
    prepared = _prepared(glyph)
    scores = sorted((min(_distance(prepared, t) for t in found), digit) for digit, found in templates.items())
    (best, digit), (second, _) = scores[0], scores[1]
    return digit if best <= MATCH and second - best >= MARGIN else None


def read_day(image: Image.Image, grid: Grid) -> date:
    """The day in the title's «на DD.MM.YYYYр.»."""
    grey = image.convert("L")
    templates, sample = _templates(grey, grid)
    strip = grey.crop((grid.hours[0][0], grid.title[0] + 1, grid.hours[-1][1], grid.title[1] - 1))
    glyphs = [part for glyph in _glyphs(strip) for part in _split(glyph, sample.width)]

    def kind(glyph: Glyph) -> str:
        if abs(glyph.height - sample.height) <= 1 and glyph.width <= 1.4 * sample.width:
            return "d"  # sized as a digit: a digit or a capital letter
        if glyph.width <= sample.width / 2 and glyph.height <= sample.height / 3:
            return "."
        return "?"

    places = [m.start() for m in re.finditer(r"(?=dd\.dd\.dddd)", "".join(kind(g) for g in glyphs))]
    if len(places) != 1:
        fail("Zakarpattia: no date DD.MM.YYYY in the image's title")
    digits = [_digit(g, templates) for i, g in enumerate(glyphs[places[0] : places[0] + 10]) if i not in (2, 5)]
    if None in digits:
        fail("Zakarpattia: the date in the image's title is not readable")
    text = "".join(digits)  # type: ignore[arg-type]
    try:
        return date(int(text[4:]), int(text[2:4]), int(text[:2]))
    except ValueError:
        fail(f"Zakarpattia: the image's title has no such day: {text[:2]}.{text[2:4]}.{text[4:]}")


def parse_image(png: bytes) -> tuple[date, Decoded]:
    """The day one schedule PNG is for, its outages by group and the groups it leaves unrecognised."""
    image = decode_png(png, SIZE, "Zakarpattia")
    grid = locate_grid(image)
    day = read_day(image, grid)
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    for group, row, colour in zip(GROUPS, grid.rows, grid.colours, strict=True):
        slots = [_slot(image, half, row, colour) for column in grid.hours for half in _halves(column)]
        if None in slots:
            unknown[group] = UNRECOGNISED
        else:
            groups[group] = outages_from_slots(day, slots)  # type: ignore[arg-type]
    return day, (groups, unknown)


class Zakarpattia(Provider):
    hosts = frozenset({"api-outage-zakarpat-energy.inneti.net"})
    min_interval = 300
    kind = Kind.PICTURE
    page = OPTIONS_URL

    def __init__(self) -> None:
        # Decoded images by path: every upload gets a new file name, so an unchanged path is
        # an unchanged image, not downloaded again.
        self._decoded: dict[str, tuple[date, Decoded]] = {}

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        shown = parse_options((await http.get(OPTIONS_URL, headers=HEADERS)).json())
        decoded: dict[str, tuple[date, Decoded]] = {}
        latest: dict[date, tuple[Shown, Decoded]] = {}
        for image in shown:
            if (result := decoded.get(image.path) or self._decoded.get(image.path)) is None:
                png = (await http.get(BASE_URL + image.path)).body
                # Reading the table and the date takes a fraction of a second: off the event loop.
                result = await asyncio.to_thread(parse_image, png)
            decoded[image.path] = result
            day, parsed = result
            if day > today + MAX_AHEAD:
                fail(f"Zakarpattia: an image is for {day:%d.%m.%Y}, which is not near")
            if not today <= day <= today + timedelta(days=1):
                log.info("Zakarpattia: %s is for %s, skipping it", image.path, day)
                continue
            # Both slots for one day: the one set last.
            if day not in latest or (latest[day][0].set_at or datetime.min.replace(tzinfo=KYIV)) < (
                image.set_at or datetime.min.replace(tzinfo=KYIV)
            ):
                latest[day] = (image, parsed)
        self._decoded = decoded
        return {
            day: DaySchedule(day, image.set_at, groups, unknown, image_url=BASE_URL + image.path)
            for day, (image, (groups, unknown)) in sorted(latest.items())
        }
