"""Spec 0092: constructions the fresh review of Part A found, planted. The failure that matters is a number a
person can't read coming back `visible`; each case here is one way to try."""
from __future__ import annotations

import time
import zlib

import pytest
from _pdfs import classes, document_of, pdf_of, raw_page, read_spans, span, stream, text_width
from dialback_ledger import pdf
from dialback_ledger.candidates import find_phones
from dialback_ledger.pdf import PdfRefused, read_pdf
from dialback_ledger.text import lines

N = "(4155550142)"      # a PDF string: the text is the digits
DIGITS = "4155550142"
E164 = "+14155550142"
AT = "72 700 Td"
OCG = b"<< /Type /OCG /Name (Layer) >>"

UNREADABLE = {
    # A crop box larger than the media box: a viewer shows only the media box.
    "number_outside_the_media_box": dict(content=f"BT /F1 10 Tf -70 700 Td [{N} -4440 (MMMMMMMMMMMMMMMMMMMM)] TJ ET",
                                         page_extra=b"/CropBox [-100 0 612 792]"),
    # A cover or clip that leaves only a sliver of each digit.
    "covered_but_for_a_sliver": dict(content=f"BT /F1 10 Tf {AT} {N} Tj ET 1 1 1 rg 70 700.4 70 12 re f"),
    "clipped_to_a_sliver": dict(content=f"q 70 699.9 70 0.5 re W n BT /F1 10 Tf {AT} {N} Tj ET Q"),
    # The last digit covered and another printed just above it, on its own line.
    "digit_swapped_by_an_overlay": dict(content=f"BT /F1 10 Tf {AT} {N} Tj ET 1 1 1 rg 122.04 700.3 5.56 9 re f "
                                                "0 0 0 rg BT /F1 10 Tf 122.04 700.6 Td (7) Tj ET"),
    # Lines of black letters drawn through the number.
    "overstruck": dict(content=f"BT /F1 10 Tf {AT} {N} Tj ET BT /F1 10 Tf 72 699.3 Td (MMMMMMMMMMMMM) Tj ET "
                               "BT /F1 10 Tf 72 700.7 Td (WWWWWWWWWWWWW) Tj ET"),
    # Ink, but no legible shape.
    "squashed_by_tz": dict(content=f"BT /F1 10 Tf 15 Tz {AT} {N} Tj ET"),
    "squashed_by_the_matrix": dict(content=f"BT /F1 10 Tf 0.15 0 0 1 72 700 Tm {N} Tj ET"),
    "stacked_by_character_spacing": dict(content=f"BT /F1 10 Tf -4.8 Tc {AT} {N} Tj ET"),
    "buried_in_a_wide_stroke": dict(content=f"5 w BT /F1 10 Tf 2 Tr {AT} {N} Tj ET"),
    # Mirrored or upside down: the x sort reads it forwards, a person doesn't.
    "mirrored_by_negative_tz": dict(content="BT /F1 10 Tf -100 Tz 200 700 Td (2410555514) Tj ET"),
    "upside_down_by_negative_size": dict(content="BT /F1 -10 Tf 200 700 Td (2410555514) Tj ET"),
    "flipped_by_negative_size_and_tz": dict(content=f"BT /F1 -10 Tf -100 Tz {AT} {N} Tj ET"),
    # Text rise: two fragments far apart on the page, on one baseline in the text matrix.
    "joined_across_the_page_by_text_rise": dict(content=f"BT /F1 10 Tf {AT} (415) Tj -300 Ts (5550142) Tj ET"),
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
