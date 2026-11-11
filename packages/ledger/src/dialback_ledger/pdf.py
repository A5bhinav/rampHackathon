"""A real PDF, read back into the page model (spec 0092, Part A).

`read_pdf(data)` returns `pages` exactly as `text.py` reads them: `{width, height, spans: [{text, x, y, size,
color}]}`, with `x` the span's first character's left edge and `y` its `top`, in points from the crop box's
top-left. So `ingest()` and everything after it are unchanged.

Visibility is decided per character (D3); the first rule that holds wins:
- `off_page`: the character's box isn't inside the page's crop box;
- `tiny`: under 4 points;
- `white`: its fill colour, converted to RGB, has every channel at F0 or above; a colour the reader can't
  convert (a pattern, a spot colour, an ICC profile) counts as white (R7);
- `no_ink`: drawing it leaves no mark (`_ink`), or the reader can't account for how it's drawn: rotated or
  mirrored, or squashed narrower than a person could read.
Characters group into lines by baseline, in x order, and a line splits into spans at a wide gap and wherever
the class changes (D2), so a span never mixes classes. Whitespace takes its neighbours' class: it has no ink
to see, and it must not break a hidden number apart. A span whose class the page model can't otherwise
express carries `ink: false`, so `visibility.classify` never reads a hidden span as visible.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence

from .visibility import NEAR_WHITE, TINY_POINTS, classify

READER_VERSION = "1"            # extractor_version is "pdf-<this>/<labeler>" (D5)
BASELINE_TOLERANCE = 0.5        # points: characters this close in baseline share a line
WORD_GAP = 1.0                  # a gap wider than this many of the left character's size splits a span (D2)
NARROW_POINTS = 0.8             # a non-whitespace character narrower than this can't be read: no_ink
_CID = re.compile(r"\(cid:\d+\)")


class PdfRefused(Exception):
    """The PDF can't be read safely (D4); `reason` says why. Fail closed: nothing of the document is stored.
    The message never holds document text."""

    def __init__(self, reason: str, *, document_id: str | None = None):
        self.reason, self.document_id = reason, document_id
        super().__init__((f"document {document_id}: " if document_id else "") + f"the PDF was refused ({reason})")

    def for_document(self, document_id: str) -> PdfRefused:
        return PdfRefused(self.reason, document_id=document_id)


def _color(char: dict) -> str | None:
    """The character's fill as #rrggbb, or None when the reader can't say what colour a person sees."""
    space, value = char.get("ncs"), char.get("non_stroking_color")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = (value,)
    if not isinstance(value, (tuple, list)) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in value):
        return None
    if space == "DeviceGray" and len(value) == 1:
        rgb = (value[0],) * 3
    elif space == "DeviceRGB" and len(value) == 3:
        rgb = tuple(value)
    elif space == "DeviceCMYK" and len(value) == 4:
        c, m, y, k = value
        rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    else:
        return None
    return "#" + "".join(f"{round(v * 255):02x}" for v in rgb)


def _white(color: str | None) -> bool:
    return color is None or all(int(color[i:i + 2], 16) >= NEAR_WHITE for i in (1, 3, 5))


def _text(char: dict) -> str:
    """An unmapped glyph (pdfminer's "(cid:N)") is kept, but holds no digits (D4)."""
    return _CID.sub("�", str(char.get("text", "")))


def _early_class(char: dict, crop: tuple[float, float, float, float]) -> str | None:
    """Every rule but ink, in D3's order; None when only the ink rule is left to decide."""
    cx0, ctop, cx1, cbottom = crop
    try:
        x0, x1, top, bottom, size = (float(char[k]) for k in ("x0", "x1", "top", "bottom", "size"))
        a, b, c, d = (float(v) for v in char["matrix"][:4])
    except (KeyError, TypeError, ValueError):
        return "off_page"
    if not all(math.isfinite(v) for v in (x0, x1, top, bottom, size, a, b, c, d)):
        return "off_page"
    if not (x0 >= cx0 and x1 <= cx1 and top >= ctop and bottom <= cbottom):
        return "off_page"
    if size < TINY_POINTS:
        return "tiny"
    if _white(_color(char)):
        return "white"
    if not (b == 0 and c == 0 and a > 0 and d > 0):
        return "no_ink"                  # rotated or mirrored: the line model can't say what a person reads
    if not _text(char).isspace() and x1 - x0 < NARROW_POINTS:
        return "no_ink"                  # squashed: 10 points tall and a smudge wide
    return None


def _ink(chars: Sequence[dict], need: set[int], pdfium_page, crop, deadline: float) -> dict[int, bool]:
    """Whether each character in `need` leaves a mark on the page (Task 4 replaces this placeholder)."""
    return {i: True for i in need}


def _lines(chars: Sequence[dict]) -> list[list[int]]:
    """Character indices grouped by baseline, top to bottom, each line in x order."""
    order = sorted(range(len(chars)), key=lambda i: -float(chars[i]["matrix"][5]))
    lines: list[list[int]] = []
    for i in order:
        if lines and abs(float(chars[lines[-1][0]]["matrix"][5]) - float(chars[i]["matrix"][5])) <= BASELINE_TOLERANCE:
            lines[-1].append(i)
        else:
            lines.append([i])
    return [sorted(line, key=lambda i: (float(chars[i]["x0"]), i)) for line in lines]


def _segments(chars: Sequence[dict], line: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for i in line:
        if out:
            prev = chars[out[-1][-1]]
            if float(chars[i]["x0"]) - float(prev["x1"]) <= WORD_GAP * float(prev["size"]):
                out[-1].append(i)
                continue
        out.append([i])
    return out


def _runs(chars: Sequence[dict], segment: list[int], cls: dict[int, str]) -> list[tuple[str, list[int]]]:
    """The segment split where the class changes; whitespace takes the class before it (after it, at the
    start). A segment of whitespace alone is dropped: it holds nothing."""
    solid = [i for i in segment if not _text(chars[i]).isspace()]
    if not solid:
        return []
    current, runs = cls[solid[0]], []
    for i in segment:
        k = current if _text(chars[i]).isspace() else cls[i]
        if runs and runs[-1][0] == k:
            runs[-1][1].append(i)
        else:
            runs.append((k, [i]))
        current = k
    return runs


def _span(chars: Sequence[dict], run: list[int], cls: str, crop, width: float, height: float) -> dict:
    first = chars[run[0]]
    span = {"text": "".join(_text(chars[i]) for i in run), "x": round(float(first["x0"]) - crop[0], 3),
            "y": round(float(first["top"]) - crop[1], 3), "size": round(float(first["size"]), 3),
            "color": _color(first) or "#ffffff"}
    if cls == "no_ink" or (cls != "visible" and classify(span, width, height) == "visible"):
        span["ink"] = False          # hidden in a way x, y, size and colour can't say: never read as visible
    return span


def _page(plumber_page, pdfium_page, deadline: float) -> dict:
    crop = tuple(float(v) for v in plumber_page.cropbox)
    width, height = crop[2] - crop[0], crop[3] - crop[1]
    chars = list(plumber_page.chars)
    cls: dict[int, str | None] = {i: _early_class(ch, crop) for i, ch in enumerate(chars)}
    need = {i for i, k in cls.items() if k is None and not _text(chars[i]).isspace()}
    ink = _ink(chars, need, pdfium_page, crop, deadline)
    for i in need:
        cls[i] = "visible" if ink.get(i) is True else "no_ink"
    for i, k in cls.items():
        if k is None:
            cls[i] = "visible"          # whitespace: its class comes from its neighbours (_runs)
    spans = [_span(chars, run, k, crop, width, height)
             for line in _lines(chars) for seg in _segments(chars, line) for k, run in _runs(chars, seg, cls)]
    return {"width": round(width, 3), "height": round(height, 3), "spans": spans}


def read_pdf(data: bytes) -> list[dict]:
    """The PDF's pages in the page model (D1)."""
    import io

    import pdfplumber
    deadline = time.monotonic() + 20.0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [_page(p, None, deadline) for p in pdf.pages]
