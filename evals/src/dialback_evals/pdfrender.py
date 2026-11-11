"""Spec 0092, D6: write any page model to a real PDF. Lab only: the product never imports this.

Each span is one text object in an embedded font, placed so that pdfplumber reads back the span's own
`x` (its first character's left edge), `y` (pdfplumber's `top`), `size` and `color`. A page model's `scan`
flag is ignored (Part A renders C04's scanned contract as a digital PDF).

Optional span fields, read only here, plant what a page model can't say:

- `trick`: `cover_rect` (a white rectangle drawn over the text), `cover_image` (a white image drawn over
  it), `invisible` (render mode 3) or `clipped` (a clip path that excludes it). A trick span's expected
  visibility is `no_ink`; the ledger's page-model path ignores the field, so its PDF reader has to find
  the trick in the PDF itself.
- Test plants for the reader's own tests: `under` (a filled rectangle of that colour drawn first, over the
  span's box), `color_space` (`gray`, `cmyk` or `separation`) with `color_value`, `hscale` (percent, the
  `Tz` operator) and `rotate` (degrees, in the text matrix). Page keys: `crop` (`[x0, top, x1, bottom]`,
  from the top-left), `rotate` (the page's `/Rotate`), `annotations` (`text`, `link`, `freetext`),
  `form_field` (an AcroForm text field) and `after` (filled rectangles drawn over the text).

The font is reportlab's bundled Bitstream Vera, whose license allows changes under another name (D10's
glyph swap). A character it can't draw fails the render.
"""
from __future__ import annotations

import functools
import io
import math
import re
from pathlib import Path

import dialback_ledger
import reportlab
from PIL import Image
from reportlab.lib.colors import PCMYKColorSep
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_PATH = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
TRICKS = ("cover_rect", "cover_image", "invisible", "clipped")
DEMO_PDFS = Path(dialback_ledger.__file__).resolve().parent / "demo_pdfs"   # the committed demo PDFs (D9)
ANNOTATIONS = ("text", "link", "freetext")
PAD = 1.0          # points around a span's box that a cover or a shape under it reaches
_COLOR = re.compile(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})")


class RenderError(ValueError):
    """The page model can't be drawn as asked (a character the font lacks, an unknown trick, a bad colour)."""


@functools.cache
def _font(font_path: Path) -> tuple[str, TTFont]:
    name = "DialbackVera" if font_path == FONT_PATH else f"Dialback-{font_path.stem}"
    font = TTFont(name, str(font_path))
    pdfmetrics.registerFont(font)
    return name, font


def text_width(text: str, size: float, *, font_path: Path = FONT_PATH) -> float:
    return pdfmetrics.stringWidth(text, _font(font_path)[0], size)


def span_box(span: dict, *, font_path: Path = FONT_PATH) -> tuple[float, float, float, float]:
    """(x0, top, x1, bottom), from the page's top-left, as pdfplumber measures the span's characters."""
    x, y, size = float(span["x"]), float(span["y"]), float(span["size"])
    return x, y, x + text_width(str(span["text"]), size, font_path=font_path), y + size


def overlaps(document: dict) -> list[str]:
    """Every pair of spans on one page whose boxes overlap (D6's precondition for the round trip)."""
    out = []
    for n, page in enumerate(document.get("pages") or [], start=1):
        spans = page.get("spans") or []
        boxes = [span_box(s) for s in spans]
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    out.append(f"{document.get('id')} page {n}: {spans[i]['text']!r} and {spans[j]['text']!r}")
    return out


def expected_visibility(span: dict, width: float, height: float) -> str:
    """What the round trip should read back for this span: `no_ink` for a trick, else the page-model rule."""
    from dialback_ledger.visibility import classify
    return "no_ink" if span.get("trick") else classify(span, width, height)


def _rgb(color: object, where: str) -> tuple[float, float, float]:
    m = _COLOR.fullmatch(str(color))
    if not m:
        raise RenderError(f"{where}: colour {color!r} isn't #rrggbb")
    return tuple(int(g, 16) / 255 for g in m.groups())


def _fill(c: canvas.Canvas, span: dict, where: str) -> None:
    space = span.get("color_space")
    if space is None:
        c.setFillColorRGB(*_rgb(span["color"], where))
        return
    value = [float(v) for v in span.get("color_value") or []]
    if space == "gray" and len(value) == 1:
        c.setFillGray(value[0])
    elif space == "cmyk" and len(value) == 4:
        c.setFillColorCMYK(*value)
    elif space == "separation" and len(value) == 4:
        c.setFillColor(PCMYKColorSep(*(100 * v for v in value), spotName="DialbackSpot", density=100))
    else:
        raise RenderError(f"{where}: colour space {space!r} with {len(value)} value(s)")


def _rect(c: canvas.Canvas, box: tuple[float, float, float, float], height: float, rgb) -> None:
    x0, top, x1, bottom = box
    c.setFillColorRGB(*rgb)
    c.rect(x0 - PAD, height - bottom - PAD, x1 - x0 + 2 * PAD, bottom - top + 2 * PAD, stroke=0, fill=1)


@functools.cache
def _white_image() -> ImageReader:
    return ImageReader(Image.new("RGB", (1, 1), (255, 255, 255)))


def _span(c: canvas.Canvas, span: dict, height: float, font: tuple[str, TTFont], font_path: Path, where: str) -> None:
    name, ttf = font
    text = str(span.get("text", ""))
    missing = sorted({ch for ch in text if ord(ch) not in ttf.face.charToGlyph})
    if missing:
        raise RenderError(f"{where}: the font can't draw {', '.join(f'U+{ord(ch):04X}' for ch in missing)} in {text!r}")
    trick = span.get("trick")
    if trick is not None and trick not in TRICKS:
        raise RenderError(f"{where}: unknown trick {trick!r} (one of {', '.join(TRICKS)})")
    x, y, size = float(span["x"]), float(span["y"]), float(span["size"])
    box = span_box(span, font_path=font_path)
    baseline = height - y - size - ttf.face.descent * size / 1000
    c.saveState()
    if span.get("under") is not None:
        _rect(c, box, height, _rgb(span["under"], where))
    if trick == "clipped":
        path = c.beginPath()
        path.rect(-10, -10, 1, 1)            # outside the page: everything drawn under it is clipped away
        c.clipPath(path, stroke=0, fill=0)
    _fill(c, span, where)
    t = c.beginText()
    t.setFont(name, size)
    if span.get("hscale") is not None:
        t.setHorizScale(float(span["hscale"]))
    if trick == "invisible":
        t.setTextRenderMode(3)
    if span.get("rotate"):
        a = math.radians(float(span["rotate"]))
        t.setTextTransform(math.cos(a), math.sin(a), -math.sin(a), math.cos(a), x, baseline)
    else:
        t.setTextOrigin(x, baseline)
    t.textOut(text)
    c.drawText(t)
    if trick == "cover_rect":
        _rect(c, box, height, (1, 1, 1))
    elif trick == "cover_image":
        x0, top, x1, bottom = box
        c.drawImage(_white_image(), x0 - PAD, height - bottom - PAD, x1 - x0 + 2 * PAD, bottom - top + 2 * PAD)
    c.restoreState()


def _page_extras(c: canvas.Canvas, page: dict, width: float, height: float, where: str) -> None:
    for shape in page.get("after") or []:          # filled rectangles drawn over the text, from the top-left
        x0, top, x1, bottom = (float(v) for v in shape["rect"])
        c.setFillColorRGB(*_rgb(shape["color"], where))
        c.rect(x0, height - bottom, x1 - x0, bottom - top, stroke=0, fill=1)
    if page.get("crop") is not None:
        x0, top, x1, bottom = (float(v) for v in page["crop"])
        c.setCropBox((x0, height - bottom, x1, height - top))
    if page.get("rotate"):
        c.setPageRotation(int(page["rotate"]))
    for k, kind in enumerate(page.get("annotations") or []):
        rect = (72, 20 + 30 * k, 200, 40 + 30 * k)
        if kind == "text":
            c.textAnnotation("Call 415-555-0142", Rect=rect, relative=0)
        elif kind == "link":
            c.linkURL("https://example.com/", rect, relative=0)
        elif kind == "freetext":
            c.freeTextAnnotation("Call 415-555-0142", "/Helv 10 Tf 0 g", Rect=rect, relative=0)
        else:
            raise RenderError(f"{where}: unknown annotation {kind!r} (one of {', '.join(ANNOTATIONS)})")
    if page.get("form_field"):
        c.acroForm.textfield(name=f"field{c.getPageNumber()}", value="415-555-0142", x=72, y=20, width=200, height=20)


def render_pdf(document: dict, *, font_path: Path = FONT_PATH, compress: bool = True,
               encrypt: str | None = None) -> bytes:
    """The document's pages as a PDF. Same input, same bytes (reportlab's invariant mode)."""
    font = _font(Path(font_path))
    pages = document.get("pages") or []
    if not pages:
        raise RenderError(f"{document.get('id')}: no pages to render")
    first = pages[0]
    buf = io.BytesIO()
    enc = None if encrypt is None else StandardEncryption(encrypt, ownerPassword="dialback-owner")
    c = canvas.Canvas(buf, pagesize=(float(first["width"]), float(first["height"])), invariant=1,
                      pageCompression=1 if compress else 0, encrypt=enc)
    for n, page in enumerate(pages, start=1):
        width, height = float(page["width"]), float(page["height"])
        c.setPageSize((width, height))
        where = f"{document.get('id')} page {n}"
        for span in page.get("spans") or []:
            _span(c, span, height, font, Path(font_path), where)
        _page_extras(c, page, width, height, where)
        c.showPage()
    c.save()
    return buf.getvalue()


def render_demo(swaps: dict[str, str] | None = None) -> dict[str, bytes]:
    """The demo seed's history documents as PDFs, by file name (D9); `swaps` puts a team phone in place of a
    vendor's fictional AR line (ref to E.164). The change requests stay JSON: they're emails. Uncompressed, so
    the bytes don't depend on the machine's zlib and `lab pdf render --check` holds everywhere."""
    from dialback_ledger.demo import load_seed, with_phone
    out = {}
    for ref, vendor in load_seed()["vendors"].items():
        swap = (vendor["phones"]["ar"], swaps[ref]) if swaps and ref in swaps else None
        for doc in vendor["documents"]:
            out[f"{doc['id']}.pdf"] = render_pdf(with_phone(doc, swap) if swap else doc, compress=False)
    return out
