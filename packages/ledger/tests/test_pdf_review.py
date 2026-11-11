"""Spec 0092: constructions the fresh review of Part A found, planted. The failure that matters is a number a
person can't read coming back `visible`; each case here is one way to try."""
from __future__ import annotations

import datetime as dt
import random
import time
import zlib

import pytest
from _pdfs import classes, document_of, pdf_of, raw_page, raw_pdf, read_spans, span, stream, text_width
from _world import CASE, DATASET, T0, VENDOR
from dialback_evals.pdfrender import FONT_PATH
from dialback_ledger import pdf
from dialback_ledger.candidates import find_phones
from dialback_ledger.ingest import ingest_all
from dialback_ledger.pdf import PdfRefused, read_pdf
from dialback_ledger.reader import StubReader
from dialback_ledger.select import ChangeRequest, DocumentRef, Sighting, select
from dialback_ledger.text import lines
from reportlab.pdfbase.pdfmetrics import stringWidth

N = "(4155550142)"      # a PDF string: the text is the digits
DIGITS = "4155550142"
E164 = "+14155550142"
AT = "72 700 Td"
OCG = b"<< /Type /OCG /Name (Layer) >>"

UNREADABLE = {
    # A crop box larger than the media box: a viewer shows only the media box.
    "number_outside_the_media_box": {"content": f"BT /F1 10 Tf -70 700 Td [{N} -4440 (MMMMMMMMMMMMMMMMMMMM)] TJ ET",
                                         "page_extra": b"/CropBox [-100 0 612 792]"},
    # A cover or clip that leaves only a sliver of each digit.
    "covered_but_for_a_sliver": {"content": f"BT /F1 10 Tf {AT} {N} Tj ET 1 1 1 rg 70 700.4 70 12 re f"},
    "clipped_to_a_sliver": {"content": f"q 70 699.9 70 0.5 re W n BT /F1 10 Tf {AT} {N} Tj ET Q"},
    # The last digit covered and another printed just above it, on its own line.
    "digit_swapped_by_an_overlay": {"content": f"BT /F1 10 Tf {AT} {N} Tj ET 1 1 1 rg 122.04 700.3 5.56 9 re f "
                                                "0 0 0 rg BT /F1 10 Tf 122.04 700.6 Td (7) Tj ET"},
    # Lines of black letters drawn through the number.
    "overstruck": {"content": f"BT /F1 10 Tf {AT} {N} Tj ET BT /F1 10 Tf 72 699.3 Td (MMMMMMMMMMMMM) Tj ET "
                               "BT /F1 10 Tf 72 700.7 Td (WWWWWWWWWWWWW) Tj ET"},
    # Ink, but no legible shape.
    "squashed_by_tz": {"content": f"BT /F1 10 Tf 15 Tz {AT} {N} Tj ET"},
    "squashed_by_the_matrix": {"content": f"BT /F1 10 Tf 0.15 0 0 1 72 700 Tm {N} Tj ET"},
    "stacked_by_character_spacing": {"content": f"BT /F1 10 Tf -4.8 Tc {AT} {N} Tj ET"},
    "buried_in_a_wide_stroke": {"content": f"5 w BT /F1 10 Tf 2 Tr {AT} {N} Tj ET"},
    # Mirrored or upside down: the x sort reads it forwards, a person doesn't.
    "mirrored_by_negative_tz": {"content": "BT /F1 10 Tf -100 Tz 200 700 Td (2410555514) Tj ET"},
    "upside_down_by_negative_size": {"content": "BT /F1 -10 Tf 200 700 Td (2410555514) Tj ET"},
    "flipped_by_negative_size_and_tz": {"content": f"BT /F1 -10 Tf -100 Tz {AT} {N} Tj ET"},
    # Text rise: two fragments far apart on the page, on one baseline in the text matrix.
    "joined_across_the_page_by_text_rise": {"content": f"BT /F1 10 Tf {AT} (415) Tj -300 Ts (5550142) Tj ET"},
}


def visible_numbers(data: bytes) -> set[str]:
    return {c.e164 for c in find_phones(lines(document_of(data))) if c.visibility == "visible"}


def test_a_plain_number_is_visible():
    # The control: the same helper, the same font, nothing done to the number.
    assert visible_numbers(raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET")) == {E164}


@pytest.mark.parametrize("case", UNREADABLE)
def test_an_unreadable_number_is_never_visible(case):
    assert E164 not in visible_numbers(raw_page(**UNREADABLE[case]))


# --- Things a viewer may show differently from the renderer ---------------------------------------------

def test_refuses_user_unit():
    # 10 points on paper is 1 point on screen at /UserUnit 0.1; the reader's sizes don't know that.
    with pytest.raises(PdfRefused) as e:
        read_pdf(raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET", page_extra=b"/UserUnit 0.1"))
    assert e.value.reason == "user_unit"


def test_refuses_optional_content():
    # Layers: an /OpenAction, or a viewer's own setting, can turn one off, and PDFium's render wouldn't show it.
    data = raw_page(f"/OC /L BDC BT /F1 10 Tf {AT} {N} Tj ET EMC", resources=b"/Properties << /L 6 0 R >>",
                    catalog_extra=b"/OCProperties << /OCGs [6 0 R] /D << /ON [6 0 R] >> >> "
                                  b"/OpenAction << /S /SetOCGState /State [/OFF 6 0 R] >>", more=(OCG,))
    with pytest.raises(PdfRefused) as e:
        read_pdf(data)
    assert e.value.reason == "layers"


def test_refuses_a_knockout_page_group():
    # A knockout group lets a fully transparent shape erase what's under it in viewers that honour it; PDFium doesn't.
    data = raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET /G gs 1 1 1 rg 60 690 100 30 re f",
                    page_extra=b"/Group << /S /Transparency /K true >>", resources=b"/ExtGState << /G << /ca 0 >> >>")
    with pytest.raises(PdfRefused) as e:
        read_pdf(data)
    assert e.value.reason == "knockout"


def test_a_plain_transparency_group_is_read():
    data = raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET", page_extra=b"/Group << /S /Transparency >>")
    assert visible_numbers(data) == {E164}


def test_refuses_a_page_whose_two_frames_disagree(monkeypatch):
    # The backstop under the crop box rule: if PDFium's page isn't the box the reader measured, the ink test's
    # window would be somewhere else on the page.
    monkeypatch.setattr(pdf, "_crop", lambda page: (0.0, 0.0, 500.0, 700.0))
    with pytest.raises(PdfRefused) as e:
        read_pdf(raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET"))
    assert e.value.reason == "page_box"


def test_a_crop_box_larger_than_the_media_box_is_the_media_box():
    (page,) = read_pdf(raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET", page_extra=b"/CropBox [-100 -100 900 900]"))
    assert (page["width"], page["height"]) == (612, 792)
    assert (round(page["spans"][0]["x"]), page["spans"][0].get("ink", True)) == (72, True)


def test_a_media_box_away_from_the_origin_still_has_ink():
    data = raw_page(f"BT /F1 10 Tf 172 700 Td {N} Tj ET", media=b"[100 100 712 892]", page_extra=b"/CropBox [150 300 650 850]")
    assert classes(data) == [(DIGITS, "visible")]


# --- Reading real PDFs -----------------------------------------------------------------------------------

ICC = stream(b"").replace(b"<< /Length 0 >>", b"<< /Length 0 /N 3 >>")


def _icc(value: str) -> bytes:
    return raw_page(f"/CS0 cs {value} sc BT /F1 10 Tf {AT} {N} Tj ET", resources=b"/ColorSpace << /CS0 [/ICCBased 6 0 R] >>",
                    more=(ICC,))


def test_a_dark_icc_colour_is_visible():
    assert visible_numbers(_icc("0.1 0.1 0.1")) == {E164}


def test_a_near_white_icc_colour_is_white():
    assert classes(_icc("0.97 0.97 0.97")) == [(DIGITS, "white")]


def test_a_ligature_doesnt_hide_the_page():
    # One "fi" glyph (octal 256 in StandardEncoding): pdfminer reads U+FB01, PDFium "f" and "i".
    data = raw_page(f"BT /F1 10 Tf {AT} (Of\\256ce {N}) Tj ET", font=b"/Encoding /StandardEncoding")
    assert visible_numbers(data) == {E164}


def test_a_slow_file_is_cut_off_at_the_limit(monkeypatch):
    # A few KB that inflate to megabytes of operators: pdfminer's parse of one page must not outrun the limit.
    monkeypatch.setattr(pdf, "TIME_LIMIT_SECONDS", 1.0)
    content = zlib.compress(b"q Q " * 2_000_000)
    body = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream"
    data = raw_page("").replace(stream(b""), body)
    started = time.monotonic()
    with pytest.raises(PdfRefused) as e:
        read_pdf(data)
    assert e.value.reason == "too_slow"
    assert time.monotonic() - started < 6, "refused, but long after the limit"


# --- Pinning the fail-closed rules -----------------------------------------------------------------------

def test_text_in_a_form_xobject_has_no_ink():
    # D3: a span the reader can't isolate counts as no_ink.
    form = (b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Length %d >>\n"
            b"stream\n" % len(f"BT /F1 10 Tf {AT} {N} Tj ET") + f"BT /F1 10 Tf {AT} {N} Tj ET".encode() + b"\nendstream")
    data = raw_page("/X Do", resources=b"/XObject << /X 6 0 R >>", more=(form,))
    assert classes(data) == [(DIGITS, "no_ink")]


def test_a_page_the_two_readers_disagree_on_has_no_ink(monkeypatch):
    real = pdf._pdfium_chars
    monkeypatch.setattr(pdf, "_pdfium_chars", lambda page: [("X", *rest) for _, *rest in real(page)])
    assert classes(raw_page(f"BT /F1 10 Tf {AT} {N} Tj ET")) == [(DIGITS, "no_ink")]


def test_one_object_partly_covered_hides_only_what_is_covered():
    # Ink is per character: the number is covered, its label on the same text object is not.
    x = 72 + 5.56 * 0 + 42.8
    data = raw_page(f"BT /F1 10 Tf {AT} (Main: line {N}) Tj ET 1 1 1 rg {x} 695 80 20 re f")
    got = classes(data)
    assert ("Main: line ", "visible") in got or ("Main: line", "visible") in got
    assert E164 not in visible_numbers(data)


def test_squashed_letters_have_no_ink():
    # 8% wide: each M is still a couple of pixels across, so the width rule is what hides it.
    assert classes(pdf_of([span("MMMMMMMM", hscale=8)])) == [("MMMMMMMM", "no_ink")]


def test_dark_gray_is_visible():
    assert classes(pdf_of([span(N, color_space="gray", color_value=[0.2])])) == [(N, "visible")]


def test_dark_cmyk_is_visible():
    assert classes(pdf_of([span(N, color_space="cmyk", color_value=[0, 0, 0, 0.9])])) == [(N, "visible")]


def _with_one_digit(**plant) -> bytes:
    head = "AR: (415) 555-01"
    x = 72 + text_width(head, 10)
    return pdf_of([span(head), span("4", x, **plant), span("2", x + text_width("4", 10))])


def test_a_tiny_digit_breaks_the_number():
    assert visible_numbers(_with_one_digit(size=3)) == set()


def test_a_near_white_digit_breaks_the_number():
    assert visible_numbers(_with_one_digit(color="#f4f4f4")) == set()


def test_a_tiny_digit_on_the_line_breaks_the_number():
    # Its bottom is level with the others (Helvetica's descent is 0.207 of the size), so it shares their line,
    # and only the per-character rule keeps it out of a visible span.
    level = 700 - 0.207 * 10 + 0.207 * 3.5
    data = raw_page(f"BT /F1 10 Tf {AT} (41555501) Tj ET BT /F1 3.5 Tf 116.48 {level} Td (4) Tj ET "
                    "BT /F1 10 Tf 118.45 700 Td (2) Tj ET")
    assert E164 not in visible_numbers(data)


def test_a_near_white_digit_on_a_dark_patch_breaks_the_number():
    # It has ink, on the black patch, so only the per-character white rule keeps it out of a visible span.
    assert visible_numbers(_with_one_digit(color="#f4f4f4", under="#000000")) == set()


def test_a_rotated_number_is_never_visible():
    assert E164 not in visible_numbers(raw_page(f"BT /F1 10 Tf 0 1 -1 0 300 300 Tm {N} Tj ET"))


@pytest.mark.parametrize("shape, want", [("#8f8f8f", "no_ink"), ("#909090", "visible")], ids=["15_levels", "16_levels"])
def test_the_ink_threshold_is_sixteen_levels(shape, want):
    assert classes(pdf_of([span(N, color="#808080", under=shape)])) == [(N, want)]


def test_the_smallest_allowed_text_has_ink():
    # D3's other pin, on the resolution: 4 point digits are still seen.
    assert classes(pdf_of([span(N, size=4)])) == [(N, "visible")]


def test_read_spans_keeps_a_visible_line_with_a_hidden_neighbour():
    got = read_spans(pdf_of([span("Main: 206.555.0117", y=86), span(N, trick="invisible")]))
    assert [s.get("ink", True) for s in got] == [True, False]


def test_a_covered_label_is_no_ink_beside_a_visible_number():
    # Ink is per character for letters too: a label a person can't see must not reach the labeler.
    data = raw_page(f"BT /F1 10 Tf {AT} (Accounts receivable: {N}) Tj ET 1 1 1 rg 70 695 92 20 re f")
    got = dict(classes(data))
    assert got.get(DIGITS) == "visible" or E164 in visible_numbers(data)
    assert not [t for t, c in classes(data) if c == "visible" and "receivable" in t]


# --- The attack on the reader (Nov 10): each case below reaches `eligible` today ----------------------------
#
# These fail on purpose, until someone decides what to do about them. A fix may hide the number or refuse the
# file, so a refusal passes. Each was rendered in PDFium, Quartz and MuPDF to see what a person sees.

LABEL = "Accounts receivable: "
X = 72 + stringWidth(LABEL, "Helvetica", 10)            # where the number starts, after its label
WIDTH = stringWidth(DIGITS, "Helvetica", 10)
LINE = f"BT /F1 10 Tf {AT} ({LABEL}{DIGITS}) Tj ET"
LABEL_ONLY = f"BT 0 0 0 rg /F1 10 Tf {AT} ({LABEL}) Tj ET "
NUMBER_ONLY = f"BT /F1 10 Tf {X} 700 Td ({DIGITS}) Tj ET"
PATCH = f"{X - 2} 695 {WIDTH + 4} 14 re f"              # a rectangle just bigger than the number


def _eligible(data: bytes) -> bool:
    """The whole path: the file as a contract and a listing, long before a change request, through select()."""
    def doc(doc_id: str, channel: str, days: int, sender=None, thread=None, subject=None) -> dict:
        return {"id": doc_id, "kind": "email" if channel == "email" else channel,
                "custody": {"channel": channel, "received_at": (T0 - dt.timedelta(days=days)).isoformat(), "sender": sender,
                            "thread_id": thread, "subject": subject, "parent_document_id": None},
                "claimed_date": None, "pages": [{"width": 612, "height": 792, "spans": [span("Please update our bank details.")]}]}

    docs = [doc("contract", "contract", 400), doc("listing", "listing", 300),
            doc("request", "email", 0, "ar@acme.example", "req", "Updated banking details")]
    try:
        result = ingest_all(docs, extractor_version="stub", reader=StubReader(), pdfs={"contract": data, "listing": data})
    except PdfRefused:
        return False
    refs = [DocumentRef(d["id"], d["custody_channel"], d["received_at"], d["sender"], d["thread_id"], d["subject"],
                        d["received_at"]) for d in result.documents]
    sightings = [Sighting(o["value_normalized"], o["document_id"], o["source_type"], o["seen_at"], o["visibility"],
                          o["role"], o["party"], o["seen_at"]) for o in result.observations]
    chosen = select(case_id=CASE, dataset_id=DATASET, vendor_id=VENDOR, vendor_domains=("acme.example",),
                    request=ChangeRequest("request", T0, "req", "Updated banking details", "ar@acme.example"),
                    requested_cutoff=T0 + dt.timedelta(hours=2), case_opened_at=T0 + dt.timedelta(hours=3),
                    documents=refs, sightings=sightings)
    return any(c["e164"] == E164 and c["eligible"] for c in chosen["candidates"])


def _one_object_per_digit(state: str) -> str:
    """The label, then each digit in its own text object at its 10 point place, after `state`."""
    digits = " ".join(f"1 0 0 1 {X + i * 5.56} 700 Tm ({d}) Tj" for i, d in enumerate(DIGITS))
    return f"{LABEL_ONLY}BT {state} {digits} ET"


def _scribble() -> str:
    """Black zigzags across the number: paths, so no character to count as overstriking."""
    out = "0 0 0 RG 0.6 w "
    for y in (699.5, 702.2, 704.9):
        points = " ".join(f"{X + i * 1.1} {y + (2.8 if i % 2 else 0)} l" for i in range(1, int(WIDTH / 1.1) + 1))
        out += f"{X} {y} m {points} S "
    return out


def _noise_image(width: int, height: int) -> bytes:
    """A one-bit image of random black and white, which averages to mid-gray at reading size."""
    rng = random.Random(1)
    per_row = (width + 7) // 8
    data = bytearray()
    for _ in range(height):
        row = bytearray(per_row)
        for x in range(width):
            if rng.random() < 0.5:
                row[x // 8] |= 0x80 >> (x % 8)
        data += row
    return (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /BitsPerComponent 1 /ColorSpace /DeviceGray /Length %d >>\n"
            b"stream\n" % (width, height, len(data)) + bytes(data) + b"\nendstream")


def _embedded(content: str, digit_width: int) -> bytes:
    """Bitstream Vera, embedded and unaltered, with /Widths saying each digit is `digit_width` wide."""
    font = FONT_PATH.read_bytes()
    widths = " ".join(str(digit_width if 48 <= c <= 57 else 600) for c in range(32, 127)).encode()
    return raw_pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        stream(content.encode()),
        b"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAA+Vera /Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 126 "
        b"/Widths [" + widths + b"] /FontDescriptor 6 0 R >>",
        b"<< /Type /FontDescriptor /FontName /AAAAAA+Vera /Flags 32 /FontBBox [-183 -236 1287 928] /ItalicAngle 0 "
        b"/Ascent 928 /Descent -236 /CapHeight 729 /StemV 80 /FontFile2 7 0 R >>",
        b"<< /Length %d /Length1 %d >>\nstream\n" % (len(font), len(font)) + font + b"\nendstream",
    )


def _two_copies_of_the_content(first: str, last: str) -> bytes:
    """Object 5 written twice. The xref points at `first`, and pdfminer follows it. The xref's entry for an unused
    object is wrong, so PDFium rebuilds the table by scanning, and the scan keeps `last`."""
    objects = [b"<< /Unused true >>", b"<< /Type /Catalog /Pages 3 0 R >>", b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 6 0 R >> >> >>",
               stream(first.encode()), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for k, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
    out += b"5 0 obj\n" + stream(last.encode()) + b"\nendobj\n"
    xref = len(out)
    offsets[0] = 3
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 2 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _deep_cover(depth: int) -> bytes:
    """A black cover in a form nested `depth` deep: past PDFium's limit, so its render never draws it."""
    forms = []
    for k in range(depth):
        last = k == depth - 1
        body = (f"0 0 0 rg {PATCH}" if last else "/X Do").encode()
        resources = b"" if last else b"/Resources << /XObject << /X %d 0 R >> >>" % (7 + k)
        forms.append(b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] " + resources + b" /Length %d >>\nstream\n" % len(body)
                     + body + b"\nendstream")
    return raw_page(f"{LINE} /X Do", resources=b"/XObject << /X 6 0 R >>", more=tuple(forms))


def _link(border: bytes) -> bytes:
    annot = b"<< /Type /Annot /Subtype /Link /Rect [%.2f 696 %.2f 710] " % (X - 1, X + WIDTH + 1) + border + b" /C [0 0 0] >>"
    return raw_page(LINE, page_extra=b"/Annots [6 0 R]", more=(annot,))


_HEAD = f"{LABEL}(415)"
_HEAD_END = 72 + stringWidth(_HEAD, "Helvetica", 10)
_NO_DATA = (b"<< /Type /XObject /Subtype /Image /Width 90 /Height 14 /BitsPerComponent 1 /ColorSpace /DeviceGray /Length 0 >>\n"
            b"stream\n\nendstream")

UNREADABLE_IN_EVERY_RENDERER = {
    # _overstruck counts only `visible` characters, and a rotation of 0.0004 makes these no_ink.
    "overstruck_by_slightly_rotated_letters": lambda: raw_page(
        f"{LINE} BT /F1 10 Tf 1 0.0004 0 1 {X} 699.3 Tm (MMMMMMM) Tj ET BT /F1 10 Tf 1 0.0004 0 1 {X} 700.7 Tm (WWWWWWW) Tj ET"),
    # _overstruck counts only characters, and these are paths.
    "overstruck_by_paths": lambda: raw_page(f"{LINE} {_scribble()}"),
    # A space 400 points tall counts as a wide word gap, so the two ends of the page read as one number.
    "joined_across_the_line_by_a_huge_space": lambda: raw_page(
        f"BT /F1 10 Tf {AT} ({_HEAD}) Tj ET BT /F1 400 Tf {_HEAD_END} {700 + 0.207 * 390} Td ( ) Tj ET "
        "BT /F1 10 Tf 450 700 Td (555-0142) Tj ET"),
    # Every pixel changes when the text goes, so it has ink, but mid-gray on noise averaging mid-gray can't be read.
    "gray_digits_on_black_and_white_noise": lambda: raw_page(
        f"{LABEL_ONLY}q 90 0 0 14 {X - 2} 695 cm /I Do Q 0.5 0.5 0.5 rg {NUMBER_ONLY}",
        resources=b"/XObject << /I 6 0 R >>", more=(_noise_image(180, 28),)),
    # Twenty levels of blue on black, and 17 levels of blue on white: the ink rule takes the biggest channel's change.
    "near_black_blue_on_a_black_patch": lambda: raw_page(f"{LABEL_ONLY}0 0 0 rg {PATCH} 0 0 0.0784 rg {NUMBER_ONLY}"),
    "palest_yellow_on_white": lambda: raw_page(f"{LABEL_ONLY}1 1 0.933 rg {NUMBER_ONLY}"),
    # An honest font with /Widths that lie. Too narrow: glyphs overlap, but their boxes don't.
    "digits_overlapped_by_narrow_widths": lambda: _embedded(f"BT /F1 10 Tf {AT} ({LABEL}{DIGITS}) Tj ET", 300),
    # Too wide, then squeezed by 5 Tz: the box passes the width floor, and each glyph is a hairline.
    "digits_squashed_behind_wide_widths": lambda: _embedded(f"BT /F1 10 Tf {AT} ({LABEL}) Tj 5 Tz ({DIGITS}) Tj ET", 6000),
}

UNREADABLE_IN_PDFIUM = {
    # PDFium's own render, and so Chrome's, shows 3 point digits; pdfminer reports 10 point ones.
    # A one-operand Tf: PDFium sets the size, pdfminer skips the operator. MuPDF agrees with PDFium, Quartz with pdfminer.
    "size_differs_by_a_one_operand_tf": lambda: raw_page(_one_object_per_digit("/F1 10 Tf 3 Tf")),
    # PDFium ends an inline image at EI/, pdfminer needs a space after EI, so only PDFium runs what follows.
    "size_differs_by_an_inline_image": lambda: raw_page(
        _one_object_per_digit("/F1 10 Tf BI /W 1 /H 1 /BPC 8 /CS /G ID x EI/F1 3 Tf EI ")),
    # The two read different copies of the content. Quartz and MuPDF show the 10 point copy.
    "size_differs_by_two_copies_of_the_content": lambda: _two_copies_of_the_content(LINE, _one_object_per_digit("/F1 3 Tf")),
}

UNREADABLE_IN_ANOTHER_VIEWER = {
    # PDFium's render shows the number, so the ink rule passes it; these viewers paint over it.
    "link_border_over_the_number_in_quartz": lambda: _link(b"/Border [0 0 14]"),
    "link_style_border_over_the_number_in_quartz": lambda: _link(b"/BS << /W 14 /S /S >>"),
    "cover_nested_past_pdfiums_limit_in_mupdf": lambda: _deep_cover(60),
    "image_with_no_data_is_black_in_mupdf": lambda: raw_page(
        f"{LINE} q {WIDTH + 4} 0 0 14 {X - 2} 695 cm /I Do Q", resources=b"/XObject << /I 6 0 R >>", more=(_NO_DATA,)),
}

STILL_CALLABLE = {
    # The controls: the same helpers, nothing hidden. A fix that fails these has over-blocked.
    "plain": lambda: raw_page(LINE),
    "one_object_per_digit": lambda: raw_page(_one_object_per_digit("/F1 10 Tf")),
    "embedded_font_with_true_widths": lambda: _embedded(f"BT /F1 10 Tf {AT} ({LABEL}{DIGITS}) Tj ET", 636),
    "link_with_no_border": lambda: _link(b"/Border [0 0 0]"),
}


@pytest.mark.parametrize("case", STILL_CALLABLE)
def test_a_readable_number_reaches_eligible(case):
    assert _eligible(STILL_CALLABLE[case]())


def test_the_helper_can_say_no():
    # The same path on a number covered outright: what passes here is what a fixed case should do.
    assert not _eligible(raw_page(f"{LINE} 1 1 1 rg {PATCH}"))


@pytest.mark.parametrize("case", UNREADABLE_IN_EVERY_RENDERER)
def test_a_number_no_renderer_shows_legibly_is_never_eligible(case):
    assert not _eligible(UNREADABLE_IN_EVERY_RENDERER[case]())


@pytest.mark.parametrize("case", UNREADABLE_IN_PDFIUM)
def test_a_number_pdfium_draws_tiny_is_never_eligible(case):
    assert not _eligible(UNREADABLE_IN_PDFIUM[case]())


@pytest.mark.parametrize("case", UNREADABLE_IN_ANOTHER_VIEWER)
def test_a_number_another_viewer_covers_is_never_eligible(case):
    assert not _eligible(UNREADABLE_IN_ANOTHER_VIEWER[case]())
