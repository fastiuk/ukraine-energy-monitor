"""Decoding schedule images from sources: untrusted input, so PNG only and tightly bounded.

Pillow does the decoding: it is the maintained standard, ships wheels for every platform we
run on, and lets us restrict decoding to the PNG plugin and check the dimensions in the
header before any pixel data is decompressed.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image

from .base import fail

#: A real schedule PNG is about 130 KB.
MAX_BYTES = 2 * 1024 * 1024
#: The largest image any source may publish (Volyn's 1750×2250 is 3.9 MP).
MAX_PIXELS = 4_000_000

# Pillow's limits (MAX_IMAGE_PIXELS, PngImagePlugin.MAX_TEXT_*) are process-wide; this library
# also runs inside Home Assistant, so it leaves them alone and checks each image itself: the
# exact expected size is read from the header before any pixel data is decompressed.


def runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """``[start, end)`` of every run of true values."""
    found, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            found.append((start, i))
            start = None
    if start is not None:
        found.append((start, len(flags)))
    return found


def _fits(value: int, expected: int | range) -> bool:
    return value == expected if isinstance(expected, int) else value in expected


def _largest(expected: int | range) -> int:
    return expected if isinstance(expected, int) else max(expected)


def _describe(expected: int | range) -> str:
    return str(expected) if isinstance(expected, int) else f"{min(expected)}–{max(expected)}"


def decode_png(data: bytes, size: tuple[int | range, int | range], what: str) -> Image.Image:
    """RGB pixels of a PNG of exactly ``size`` (either side may be a range); ``SourceFormatError`` otherwise."""
    if len(data) > MAX_BYTES:
        fail(f"{what}: image is {len(data)} bytes, over {MAX_BYTES}")
    widths, heights = size
    try:
        image = Image.open(io.BytesIO(data), formats=["PNG"])
        # Only the header has been read so far: check it before inflating pixel data.
        width, height = image.size
        if not (_fits(width, widths) and _fits(height, heights)) or _largest(widths) * _largest(heights) > MAX_PIXELS:
            fail(f"{what}: image is {width}×{height}, expected {_describe(widths)}×{_describe(heights)}")
        if image.mode in ("RGBA", "LA", "P") or "transparency" in image.info:
            # Transparent pixels read as the white page they are shown on. RGB and greyscale
            # images mark them with a tRNS colour, which only the conversion to RGBA applies.
            rgba = image.convert("RGBA")
            page = Image.new("RGB", image.size, (255, 255, 255))
            page.paste(rgba, mask=rgba.getchannel("A"))
            return page
        return image.convert("RGB")
    except Image.DecompressionBombError as err:
        fail(f"{what}: image is too large ({err})")
    except (OSError, SyntaxError, ValueError, EOFError) as err:
        # Pillow reports corrupt or foreign data as any of these.
        fail(f"{what}: not a readable PNG ({type(err).__name__}: {err})")
