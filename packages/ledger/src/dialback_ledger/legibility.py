"""Could a person read this phone number? (spec 0092, D10a)

The ink rule in `pdf.py` asks whether anything changed on the page, which isn't whether a person can read it. So
every `visible` phone candidate in a digital PDF must also pass four tests on the page PDFium renders:

1. Contrast: its digits' ink against what's under that ink, at least 3:1.
2. Size: its digits measure, in pixels, at least what a 4 point font's digits measure.
3. Together: no gap between its digits is wider than two digit heights.
4. Read: Tesseract reads the same digits, in the same order, at a confidence of at least `C_MIN`.

OCR binarizes adaptively, so left alone it would read what a person can't. It gets only an image in which pixels
below the contrast floor are set to what's under them. The ratio, the 4 point floor and the gap are the spec's;
the render size, margin and `C_MIN` are development settings, chosen with the known attacks in view.
"""
from __future__ import annotations

import functools
import html
import itertools
import math
import re
import shutil
import statistics
import time
from collections.abc import Collection, Sequence
from dataclasses import astuple, dataclass

import numpy as np
import pypdfium2.raw as pdfium_c

from .reader import ReaderUnavailable
from .visibility import TINY_POINTS

CONTRAST_FLOOR = 3.0                         # the WCAG ratio for large text
MIN_DIGIT_HEIGHT = TINY_POINTS * 0.65        # points: a digit is about 0.65 of its font's size or more
MAX_GAP_HEIGHTS = 2.0
INK_MIN = 8                                  # a pixel is the digit's ink when a channel moves by this much

# Development settings (D10a).
OCR_DIGIT_PIXELS = 40                        # digits are rendered about this tall for OCR
OCR_MARGIN_HEIGHTS = 0.5                     # digit heights of page around the number: more lets the next line in
OCR_PAD_PIXELS = 20                          # blank border Tesseract needs around a tight crop
OCR_CONFIG = "--psm 7 -c hocr_char_boxes=1"  # one line, a confidence per character. A whitelist zeroes every confidence
OCR_TIMEOUT_SECONDS = 5.0
C_MIN = 80.0                                 # per digit. Development numbers read right scored 88.6 or more
_HOCR_CHAR = re.compile(r"<span class='ocrx_cinfo' title='[^']*x_conf ([0-9.]+)'>(.*?)</span>", re.DOTALL)


@dataclass(frozen=True)
class Verdict:
    contrast: bool
    size: bool
    together: bool
    read: bool

    def __bool__(self) -> bool:
        return all(astuple(self))


@functools.cache
def tesseract_version() -> str | None:
    if shutil.which("tesseract") is None:
        return None
    import pytesseract
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception:  # noqa: BLE001  a Tesseract that can't say its version can't be relied on (R7)
        return None


def real_require_ocr() -> None:
    """Stop before reading anything when Tesseract 5 isn't here (R7)."""
    version = tesseract_version()
    if version is None or not version.startswith("5"):
        raise ReaderUnavailable(f"reading a PDF needs Tesseract 5, and found {version or 'none'}: refusing to start")


require_ocr = real_require_ocr


def _ocr(image: np.ndarray, timeout: float) -> list[tuple[str, float]]:
    """Tesseract's characters on `image`, each with its confidence (0 to 100), in reading order. Per character,
    because a word's confidence is dragged down by the punctuation in it: a `+` misread as `-` scored `-1` at 17."""
    import pytesseract
    from PIL import Image, ImageOps
    gray = Image.fromarray(image).convert("L")
    edge = np.concatenate([np.asarray(gray)[0], np.asarray(gray)[-1]])
    padded = ImageOps.expand(gray, border=OCR_PAD_PIXELS, fill=int(np.median(edge)))
    hocr = pytesseract.image_to_pdf_or_hocr(padded, lang="eng", config=OCR_CONFIG, extension="hocr", timeout=timeout)
    return [(html.unescape(text), float(conf)) for conf, text in _HOCR_CHAR.findall(hocr.decode("utf-8"))]


def real_read_matches(image: np.ndarray, expected: str, timeout: float) -> bool:
    try:
        words = _ocr(image, timeout)
    except Exception:  # noqa: BLE001  an OCR error or timeout is an unconfirmed number (R7)
        return False
    confidences = [conf for word, conf in words if any(ch.isdigit() for ch in word)]
    read = "".join(ch for word, _ in words for ch in word if ch.isdigit())
    return bool(confidences) and read == expected and min(confidences) >= C_MIN


_read_matches = real_read_matches


def _luminance(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255
    linear = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def _contrast(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    la, lb = _luminance(a), _luminance(b)
    return (np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)


def _below_floor_to_background(shown: np.ndarray, background: np.ndarray, contrast: np.ndarray) -> np.ndarray:
    """What OCR may see: the page, with ink too faint for a person put back to what's under it."""
    out = shown.copy()
    weak = contrast < CONTRAST_FLOOR
    out[weak] = background[weak]
    return out


def _render(pdfium_page, box, crop, scale: float) -> np.ndarray:
    x0, top, x1, bottom = box
    cx0, ctop, cx1, cbottom = crop
    bitmap = pdfium_page.render(scale=scale, crop=(x0 - cx0, cbottom - bottom, cx1 - x1, top - ctop),
                                may_draw_forms=False, rev_byteorder=True)
    return np.ascontiguousarray(np.asarray(bitmap.to_numpy())[:, :, :3])


def assess(pdfium_page, crop, page_height: float, glyphs: Sequence[tuple[float, float, float, float]],
           objects: Collection[int], expected: str, deadline: float) -> Verdict:
    """The four tests for one number. `glyphs` are its digits' boxes from PDFium (left, bottom, right, top, in PDF
    space); `objects` the indices of the top-level text objects drawing them; `expected` its digits."""
    boxes = [(left, page_height - top, right, page_height - bottom) for left, bottom, right, top in glyphs]
    digit = max(statistics.median(b[3] - b[1] for b in boxes), 0.1)
    scale = min(max(OCR_DIGIT_PIXELS / digit, 2.0), 16.0)
    margin = OCR_MARGIN_HEIGHTS * digit
    box = (max(crop[0], min(b[0] for b in boxes) - margin), max(crop[1], min(b[1] for b in boxes) - margin),
           min(crop[2], max(b[2] for b in boxes) + margin), min(crop[3], max(b[3] for b in boxes) + margin))
    shown = _render(pdfium_page, box, crop, scale)
    page_objects = list(pdfium_page.get_objects(max_depth=0))
    hidden = [(page_objects[k].raw, pdfium_c.FPDFTextObj_GetTextRenderMode(page_objects[k].raw)) for k in objects]
    for obj, _ in hidden:
        pdfium_c.FPDFTextObj_SetTextRenderMode(obj, pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE)
    try:
        background = _render(pdfium_page, box, crop, scale)
    finally:
        for obj, mode in hidden:
            pdfium_c.FPDFTextObj_SetTextRenderMode(obj, mode)
    if shown.shape != background.shape:
        return Verdict(False, False, False, False)
    contrast = _contrast(shown, background)
    inked = np.abs(shown.astype(np.int16) - background.astype(np.int16)).max(axis=2) >= INK_MIN
    faint, heights, spans = False, [], []
    for x0, top, x1, bottom in boxes:
        cols = slice(max(math.floor((x0 - box[0]) * scale), 0), max(math.ceil((x1 - box[0]) * scale), 0))
        rows = slice(max(math.floor((top - box[1]) * scale), 0), max(math.ceil((bottom - box[1]) * scale), 0))
        ink = inked[rows, cols]
        if not ink.any():
            faint = True
            heights.append(0.0)
            continue
        faint = faint or float(np.percentile(contrast[rows, cols][ink], 75)) < CONTRAST_FLOOR
        filled_rows, filled_cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        heights.append((filled_rows[-1] - filled_rows[0] + 1) / scale)
        spans.append((cols.start + filled_cols[0], cols.start + filled_cols[-1] + 1))
    spans.sort()
    widest = max((b[0] - a[1] for a, b in itertools.pairwise(spans)), default=0) / scale
    timeout = max(min(OCR_TIMEOUT_SECONDS, deadline - time.monotonic()), 0.1)
    read = _read_matches(_below_floor_to_background(shown, background, contrast), expected, timeout)
    return Verdict(contrast=not faint, size=bool(min(heights) >= MIN_DIGIT_HEIGHT),
                   together=bool(widest <= MAX_GAP_HEIGHTS * statistics.median(heights)), read=read)
