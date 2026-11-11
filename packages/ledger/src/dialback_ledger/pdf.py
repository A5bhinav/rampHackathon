"""A real PDF, read back into the page model (spec 0092, Part A).

`read_pdf(data)` returns `pages` exactly as `text.py` reads them: `{width, height, spans: [{text, x, y, size,
color}]}`, with `x` the span's first character's left edge and `y` its `top`, in points from the crop box's
top-left. So `ingest()` and everything after it are unchanged.

Visibility is decided per character (D3); the first rule that holds wins:
- `off_page`: the character's box isn't inside the page's crop box;
- `tiny`: under 4 points;
- `white`: its fill colour, converted to RGB, has every channel at F0 or above; a colour the reader can't
  convert (a pattern, a spot colour, an ICC profile) counts as white (R7);
- `no_ink`: drawing it leaves no mark a person could read (`_ink`), or the reader can't account for how it's
  drawn: rotated or mirrored, squashed, stroked, or a digit overlapped by other text.
Characters group into lines by baseline, in x order, and a line splits into spans at a wide gap and wherever
the class changes (D2), so a span never mixes classes. Whitespace takes its neighbours' class: it has no ink
to see, and it must not break a hidden number apart. A span whose class the page model can't otherwise
express carries `ink: false`, so `visibility.classify` never reads a hidden span as visible.
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import math
import re
import signal
import threading
import time
import unicodedata
from collections.abc import Iterator, Sequence

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfminer.pdftypes import resolve1

from .visibility import NEAR_WHITE, TINY_POINTS, classify

READER_VERSION = "1"            # extractor_version is "pdf-<this>/<labeler>" (D5)
LINE_TOLERANCE = 0.5            # points: characters whose bottoms are this close share a line
WORD_GAP = 1.0                  # a gap wider than this many of the left character's size splits a span (D2)
NARROW_SHARE = 0.1              # a character narrower than this share of its size can't be read: no_ink
DIGIT_NARROW_SHARE = 0.3        # nor a digit narrower than this (real digits are about half their size or more)
MAX_PAGES = 20                  # D4: a longer document is refused
MAX_BYTES = 10 * 1024 * 1024    # D4: so is a bigger file (10 MB)
TIME_LIMIT_SECONDS = 20.0       # D4: and reading that takes longer
INK_SCALE = 3                   # the ink check renders at 216 dpi, so small glyphs still cover whole pixels
INK_DIFF = 16                   # a pixel has ink when a channel moves by this much: the white rule's own margin
INK_MARGIN = 2.0                # points rendered around an object's characters
INK_SPREAD = 0.75               # a digit's visible ink must reach this share of its glyph's rows and columns
OVERLAP_LIMIT = 0.25            # a digit with more than this share of its box under other visible text: no_ink
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
    if space == "ICCBased":          # read by its component count; the ink rule renders the real colour
        space = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(len(value))
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
    return _CID.sub("\ufffd", str(char.get("text", "")))


def _early_class(char: dict, crop: tuple[float, float, float, float]) -> str | None:
    """Every rule but ink, in D3's order; None when only the ink rule is left to decide."""
    cx0, ctop, cx1, cbottom = crop
    try:
        x0, x1, top, bottom, size, adv = (float(char[k]) for k in ("x0", "x1", "top", "bottom", "size", "adv"))
        a, b, c, d = (float(v) for v in char["matrix"][:4])
    except (KeyError, TypeError, ValueError):
        return "off_page"
    if not all(math.isfinite(v) for v in (x0, x1, top, bottom, size, adv, a, b, c, d)):
        return "off_page"
    if not (x0 >= cx0 and x1 <= cx1 and top >= ctop and bottom <= cbottom):
        return "off_page"
    if size < TINY_POINTS:
        return "tiny"
    if _white(_color(char)):
        return "white"
    if not (b == 0 and c == 0 and a > 0 and d > 0 and adv > 0):
        return "no_ink"                  # rotated or mirrored: the line model can't say what a person reads
    text = _text(char)
    if not text.isspace() and x1 - x0 < (DIGIT_NARROW_SHARE if text.isdigit() else NARROW_SHARE) * size:
        return "no_ink"                  # squashed: 10 points tall and a smudge wide
    return None


def _pdfium_chars(pdfium_page) -> list[tuple[str, int | None, tuple[float, float, float, float]]]:
    """The page's characters as PDFium reads them, without the spaces and line breaks it adds and without
    whitespace, each with the index of the top-level text object that draws it (None: no object, or one nested
    in a form, which the ink check can't hide on its own) and its glyph's own box (left, bottom, right, top)."""
    objects = {ctypes.addressof(o.raw.contents): k for k, o in enumerate(pdfium_page.get_objects(max_depth=0))}
    textpage = pdfium_page.get_textpage()
    try:
        out = []
        for i in range(textpage.count_chars()):
            if pdfium_c.FPDFText_IsGenerated(textpage.raw, i) != 0:
                continue
            ch = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, i))
            if ch.isspace():
                continue
            obj = pdfium_c.FPDFText_GetTextObject(textpage.raw, i)
            out.append((ch, objects.get(ctypes.addressof(obj.contents)) if obj else None, textpage.get_charbox(i)))
        return out
    finally:
        textpage.close()


def _render(pdfium_page, box, crop) -> np.ndarray:
    x0, top, x1, bottom = box
    cx0, ctop, cx1, cbottom = crop
    bitmap = pdfium_page.render(scale=INK_SCALE, crop=(x0 - cx0, cbottom - bottom, cx1 - x1, top - ctop),
                                may_draw_forms=False)
    return np.asarray(bitmap.to_numpy(), dtype=np.int16)


def _align(chars: Sequence[dict], solid: list[int], theirs: list) -> dict[int, tuple] | None:
    """Pair each of pdfplumber's characters with PDFium's, in order; None when the two readings disagree. A
    ligature can be one character in one reading and several in the other."""
    nfkc = lambda s: unicodedata.normalize("NFKC", s)
    paired: dict[int, tuple] = {}
    j = 0
    for i in solid:
        mine = _text(chars[i])
        if j >= len(theirs):
            return None
        if mine == "\ufffd" or nfkc(theirs[j][0]) == nfkc(mine):
            take = 1
        else:
            take = len(nfkc(mine))
            if "".join(nfkc(u) for u, *_ in theirs[j:j + take]) != nfkc(mine):
                return None
        paired[i] = theirs[j]
        j += take
    return paired if j == len(theirs) else None


def _object_is_plain(obj) -> bool:
    """Filled text at a positive size. A stroke can bury a glyph in ink, and a negative size flips it in a way
    pdfminer's numbers don't always show."""
    size = ctypes.c_float()
    return (pdfium_c.FPDFTextObj_GetTextRenderMode(obj) == pdfium_c.FPDF_TEXTRENDERMODE_FILL
            and bool(pdfium_c.FPDFTextObj_GetFontSize(obj, ctypes.byref(size))) and size.value > 0)


def _digit_has_ink(diff: np.ndarray, glyph, box, page_height: float) -> bool:
    """A digit's ink has to reach across its glyph, not just exist: a digit is one connected shape, so every row
    and column of its own box holds ink, and a cover or clip that leaves a sliver leaves most of them empty."""
    left, bottom, right, top = glyph
    px0 = max(math.floor((left - box[0]) * INK_SCALE), 0)
    px1 = max(math.ceil((right - box[0]) * INK_SCALE), 0)
    py0 = max(math.floor((page_height - top - box[1]) * INK_SCALE), 0)
    py1 = max(math.ceil((page_height - bottom - box[1]) * INK_SCALE), 0)
    cell = diff[py0:py1, px0:px1] >= INK_DIFF
    return bool(cell.size and cell.any(axis=1).mean() >= INK_SPREAD and cell.any(axis=0).mean() >= INK_SPREAD)


def _ink(chars: Sequence[dict], need: set[int], pdfium_page, crop, deadline: float, page_height: float) -> dict[int, bool]:
    """Whether each character in `need` leaves a mark on the page (D3). The page is rendered as it is and with
    the text object that draws the character hidden; the character has ink when the two renders differ inside
    its own box, and a digit only when they differ across most of its glyph. Comparing with and without the
    text, not the box against its surroundings, catches text drawn under a shape or an image, in invisible mode,
    clipped away, or in the colour of what's under it. A character the reader can't tie to its own text object
    has no ink (R7)."""
    solid = [i for i, ch in enumerate(chars) if not _text(ch).isspace()]
    paired = _align(chars, solid, _pdfium_chars(pdfium_page))
    if paired is None:
        return {i: False for i in need}              # the two readings of the page disagree: can't isolate
    by_object: dict[int, list[int]] = {}
    for i in need:
        if paired[i][1] is not None:
            by_object.setdefault(paired[i][1], []).append(i)
    ink = {i: False for i in need}
    objects = list(pdfium_page.get_objects(max_depth=0))
    cx0, ctop, cx1, cbottom = crop
    for k, members in sorted(by_object.items()):
        box = (max(cx0, min(float(chars[i]["x0"]) for i in members) - INK_MARGIN),
               max(ctop, min(float(chars[i]["top"]) for i in members) - INK_MARGIN),
               min(cx1, max(float(chars[i]["x1"]) for i in members) + INK_MARGIN),
               min(cbottom, max(float(chars[i]["bottom"]) for i in members) + INK_MARGIN))
        obj = objects[k].raw
        if not _object_is_plain(obj):
            continue                                  # no ink (R7)
        mode = pdfium_c.FPDFTextObj_GetTextRenderMode(obj)
        before = _render(pdfium_page, box, crop)
        pdfium_c.FPDFTextObj_SetTextRenderMode(obj, pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE)
        try:
            after = _render(pdfium_page, box, crop)
        finally:
            pdfium_c.FPDFTextObj_SetTextRenderMode(obj, mode)
        if before.shape != after.shape:
            continue                                  # can't compare: no ink (R7)
        diff = np.abs(before - after).max(axis=2)
        for i in members:
            ch = chars[i]
            px0 = math.floor((float(ch["x0"]) - box[0]) * INK_SCALE) + 1
            px1 = math.ceil((float(ch["x1"]) - box[0]) * INK_SCALE) - 1
            py0 = math.floor((float(ch["top"]) - box[1]) * INK_SCALE)
            py1 = math.ceil((float(ch["bottom"]) - box[1]) * INK_SCALE)
            cell = diff[max(py0, 0):max(py1, 0), max(px0, 0):max(px1, 0)]
            ink[i] = bool(cell.size) and int(cell.max()) >= INK_DIFF
            if ink[i] and _text(ch).isdigit():
                ink[i] = _digit_has_ink(diff, paired[i][2], box, page_height)
        if time.monotonic() > deadline:
            raise PdfRefused("too_slow")
    return ink


def _lines(chars: Sequence[dict]) -> list[list[int]]:
    """Character indices grouped by where they are on the page, top to bottom, each line in x order. By each
    character's real position, not its text matrix: text rise moves a character without moving the matrix."""
    order = sorted(range(len(chars)), key=lambda i: float(chars[i]["bottom"]))
    lines: list[list[int]] = []
    for i in order:
        if lines and abs(float(chars[lines[-1][0]]["bottom"]) - float(chars[i]["bottom"])) <= LINE_TOLERANCE:
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


def _crop(plumber_page) -> tuple[float, float, float, float]:
    """What a viewer shows: the crop box inside the media box."""
    c, m = plumber_page.cropbox, plumber_page.mediabox
    return float(max(c[0], m[0])), float(max(c[1], m[1])), float(min(c[2], m[2])), float(min(c[3], m[3]))


def _overstruck(chars: Sequence[dict], cls: dict[int, str]) -> set[int]:
    """Digits with too much of their box under other visible text: stacked by character spacing, or drawn
    through by another line. A person can't read them, whatever each one's ink says."""
    shown = sorted((i for i, k in cls.items() if k == "visible" and not _text(chars[i]).isspace()),
                   key=lambda i: float(chars[i]["x0"]))
    box = {i: tuple(float(chars[i][k]) for k in ("x0", "top", "x1", "bottom")) for i in shown}
    out: set[int] = set()
    for n, i in enumerate(shown):
        a = box[i]
        for j in shown[n + 1:]:
            b = box[j]
            if b[0] >= a[2]:
                break
            area = max(min(a[2], b[2]) - max(a[0], b[0]), 0) * max(min(a[3], b[3]) - max(a[1], b[1]), 0)
            for me, other in ((i, a), (j, b)):
                size = (other[2] - other[0]) * (other[3] - other[1])
                if _text(chars[me]).isdigit() and size > 0 and area / size > OVERLAP_LIMIT:
                    out.add(me)
    return out


def _page(plumber_page, pdfium_page, deadline: float) -> dict:
    crop = _crop(plumber_page)
    width, height = crop[2] - crop[0], crop[3] - crop[1]
    if max(abs(a - b) for a, b in zip(pdfium_page.get_size(), (width, height))) > 0.5:
        raise PdfRefused("page_box")     # PDFium's page isn't this box, so the ink test's window would be elsewhere
    chars = list(plumber_page.chars)
    cls: dict[int, str | None] = {i: _early_class(ch, crop) for i, ch in enumerate(chars)}
    need = {i for i, k in cls.items() if k is None and not _text(chars[i]).isspace()}
    ink = _ink(chars, need, pdfium_page, crop, deadline, float(plumber_page.height))
    for i in need:
        cls[i] = "visible" if ink.get(i) is True else "no_ink"
    for i in _overstruck(chars, cls):
        cls[i] = "no_ink"
    for i, k in cls.items():
        if k is None:
            cls[i] = "visible"          # whitespace: its class comes from its neighbours (_runs)
    spans = [_span(chars, run, k, crop, width, height)
             for line in _lines(chars) for seg in _segments(chars, line) for k, run in _runs(chars, seg, cls)]
    return {"width": round(width, 3), "height": round(height, 3), "spans": spans}


def _refuse_page(plumber_page, pdfium_page) -> None:
    """D4, per page: a rotated page (pdfminer and PDFium would disagree on its frame), an annotation or form
    field a person may read that the text layer lacks (a Link shows no text), or no characters at all, which is
    a scan (Part B; scans are off)."""
    if pdfium_page.get_rotation() != 0:
        raise PdfRefused("rotated")
    if resolve1(plumber_page.page_obj.attrs.get("UserUnit", 1)) != 1:
        raise PdfRefused("user_unit")    # the reader's sizes would be off by that factor
    group = resolve1(plumber_page.page_obj.attrs.get("Group"))
    if isinstance(group, dict) and resolve1(group.get("K")):
        raise PdfRefused("knockout")     # an invisible shape can erase text in viewers that honour it; PDFium doesn't
    for i in range(pdfium_c.FPDFPage_GetAnnotCount(pdfium_page.raw)):
        annot = pdfium_c.FPDFPage_GetAnnot(pdfium_page.raw, i)
        try:
            subtype = pdfium_c.FPDFAnnot_GetSubtype(annot)
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)
        if subtype == pdfium_c.FPDF_ANNOT_WIDGET:
            raise PdfRefused("form_field")
        if subtype != pdfium_c.FPDF_ANNOT_LINK:
            raise PdfRefused("annotation")
    if not plumber_page.chars:
        raise PdfRefused("scan")


def _why_unloadable(data: bytes) -> str:
    """PDFium won't load the file: encrypted, or unparseable. Asked of pdfminer, because PDFium's last error can
    be left over from an earlier file."""
    from pdfminer.pdfdocument import PDFDocument, PDFPasswordIncorrect
    from pdfminer.pdfparser import PDFParser
    try:
        return "encrypted" if PDFDocument(PDFParser(io.BytesIO(data))).encryption else "unparseable"
    except PDFPasswordIncorrect:
        return "encrypted"
    except Exception:  # noqa: BLE001  any other parse failure: unparseable (R7)
        return "unparseable"


def read_pdf(data: bytes) -> list[dict]:
    """The PDF's pages in the page model (D1), or `PdfRefused` (D4): an encrypted or unparseable file, over
    MAX_PAGES pages or MAX_BYTES bytes, reading that takes over TIME_LIMIT_SECONDS, a page with annotations or
    form fields, a rotated or rescaled page, layers, or a page with no characters (a scan)."""
    if not isinstance(data, (bytes, bytearray)):
        raise PdfRefused("unparseable")
    if len(data) > MAX_BYTES:
        raise PdfRefused("too_big")
    with _hard_limit(TIME_LIMIT_SECONDS):
        return _read(bytes(data), time.monotonic() + TIME_LIMIT_SECONDS)


@contextlib.contextmanager
def _hard_limit(seconds: float) -> Iterator[None]:
    """Stop a read that outruns the limit, even inside one long page parse, where the checks between steps can't
    reach (a few KB can inflate to megabytes of operators). A timer signal, so main thread only; elsewhere those
    checks are all there is. It repeats, in case a library's own `except` swallows the first."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def expired(*_: object) -> None:
        raise PdfRefused("too_slow")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(seconds, 0.001), 0.25)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _read(data: bytes, deadline: float) -> list[dict]:
    try:
        document = pdfium.PdfDocument(data)
    except PdfRefused:
        raise
    except Exception:  # noqa: BLE001  PDFium can't load it: refused, never read (R7)
        raise PdfRefused("too_slow" if time.monotonic() > deadline else _why_unloadable(data)) from None
    try:
        if pdfium_c.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            raise PdfRefused("encrypted")                # encrypted, even with an empty user password
        count = len(document)
        if count == 0:
            raise PdfRefused("unparseable")              # PDFium itself won't load one; this is the backstop
        if count > MAX_PAGES:
            raise PdfRefused("too_many_pages")
        if pdfium_c.FPDF_GetFormType(document.raw) != pdfium_c.FORMTYPE_NONE:
            raise PdfRefused("form_field")
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) != count:
                raise PdfRefused("unparseable")          # the two readers disagree on the document itself
            if "OCProperties" in pdf.doc.catalog:
                raise PdfRefused("layers")               # a viewer can turn a layer off where PDFium's render shows it
            pages = []
            for n, plumber_page in enumerate(pdf.pages):
                pdfium_page = document[n]
                _refuse_page(plumber_page, pdfium_page)
                pages.append(_page(plumber_page, pdfium_page, deadline))
                if time.monotonic() > deadline:
                    raise PdfRefused("too_slow")
            return pages
    except PdfRefused:
        raise
    except Exception:  # noqa: BLE001  anything else the reader can't account for is refused (R7)
        # pdfplumber wraps what's raised inside its parse, the timer's refusal included.
        raise PdfRefused("too_slow" if time.monotonic() > deadline else "unparseable") from None
    finally:
        document.close()
