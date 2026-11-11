"""Spec 0092, D10: a font whose outlines disagree with its character map shows one digit and extracts another: there
is ink, but not the digits the text layer holds. The legibility check (D10a) reads the page, so it catches it."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from dialback_evals import known
from dialback_evals.pdfrender import FONT_PATH, render_pdf, text_width
from dialback_ledger.candidates import find_phones
from dialback_ledger.pdf import read_pdf
from dialback_ledger.text import lines
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
KNOWN = ROOT / "evals" / "known-failures.json"
FINDINGS = ROOT / "evals" / "FINDINGS.md"
TEXT_LAYER = "AR direct: (415) 555-0138"     # what the character map says
WHAT_A_PERSON_SEES = "+14155550183"          # the 3 and the 8 swap on the page


@pytest.fixture(scope="module")
def swapped_font(tmp_path_factory) -> Path:
    """Vera with the outlines of 3 and 8 swapped. Vera's license allows changes under another name."""
    font = TTFont(FONT_PATH)
    glyf = font["glyf"]
    three, eight = glyf["three"], glyf["eight"]
    glyf["three"], glyf["eight"] = eight, three
    hmtx = font["hmtx"]
    hmtx["three"], hmtx["eight"] = hmtx["eight"], hmtx["three"]     # each outline keeps its own metrics
    for record in font["name"].names:
        if record.nameID in (1, 3, 4, 6, 16):
            record.string = "DialbackSwapped"
    path = tmp_path_factory.mktemp("font") / "DialbackSwapped.ttf"
    font.save(path)
    return path


def _doc(text: str) -> dict:
    return {"id": "glyph-swap", "kind": "contract", "pages": [{"width": 612, "height": 792, "spans": [
        {"text": text, "x": 72, "y": 100, "size": 10, "color": "#000000"}]}]}


def _failing(data: bytes, seen: str) -> dict[str, set[str]]:
    found = find_phones(lines({"pages": read_pdf(data)}))
    assert found, "the reader found the number"
    wrong = [c for c in found if c.visibility == "visible" and c.e164 != seen]
    return {"glyph-swap": {"text_layer_disagrees_with_glyphs"}} if wrong else {}


def test_glyph_swap(swapped_font):
    # D10a's check reads the page: Tesseract sees 0183 where the text layer says 0138, so the number is unverified.
    assert _failing(render_pdf(_doc(TEXT_LAYER), font_path=swapped_font), WHAT_A_PERSON_SEES) == {}
    found = find_phones(lines({"pages": read_pdf(render_pdf(_doc(TEXT_LAYER), font_path=swapped_font))}))
    assert [c.visibility for c in found] == ["unverified"]
    assert not [e for e in known.for_job(KNOWN, "lab-pdf") if e.case == "glyph-swap"], "it comes off the list"


def test_an_honest_font_is_not_a_failure():
    assert _failing(render_pdf(_doc("AR direct: (415) 555-0183")), WHAT_A_PERSON_SEES) == {}


def _bitmap(text: str, font: Path) -> np.ndarray:
    data = render_pdf({"id": "t", "kind": "contract", "pages": [{"width": 40, "height": 30, "spans": [
        {"text": text, "x": 5, "y": 5, "size": 16, "color": "#000000"}]}]}, font_path=font)
    return np.asarray(pdfium.PdfDocument(data)[0].render(scale=3).to_numpy())


def test_glyph_swap_plant_really_swaps(swapped_font):
    # So the plant can fail: on the page, the swapped font's 3 is the honest font's 8, and not its 3.
    assert np.array_equal(_bitmap("3", swapped_font), _bitmap("8", FONT_PATH))
    assert np.array_equal(_bitmap("8", swapped_font), _bitmap("3", FONT_PATH))
    assert not np.array_equal(_bitmap("3", swapped_font), _bitmap("3", FONT_PATH))


# A patch inside a digit: the same family. White rectangles over the left side of an 8, between its bars, leave a
# 3. The digit still has ink across its rows and columns, so the ink rule passes it; reading the pixels catches it.
HEAD = "AR direct: (415) 555-018"
PATCHED = "+14155550183"


def _patched(with_patches: bool) -> dict:
    doc = _doc(HEAD + "8")
    x = 72 + text_width(HEAD, 10)
    if with_patches:
        doc["pages"][0]["after"] = [{"rect": [x, 101.3, x + 2.9, 103.2], "color": "#ffffff"},
                                    {"rect": [x, 104.6, x + 2.9, 106.6], "color": "#ffffff"}]
    return doc


def test_digit_patch():
    # The legibility check reads the page, so the patched digit doesn't confirm.
    found = find_phones(lines({"pages": read_pdf(render_pdf(_patched(True)))}))
    assert [c.visibility for c in found] == ["unverified"]
    assert not [e for e in known.for_job(KNOWN, "lab-pdf") if e.case == "digit-patch"], "it comes off the list"


def test_the_same_digit_unpatched_is_visible():
    found = find_phones(lines({"pages": read_pdf(render_pdf(_patched(False)))}))
    assert [c.visibility for c in found] == ["visible"]


def test_digit_patch_really_changes_the_digit():
    def picture(doc: dict) -> np.ndarray:
        return np.asarray(pdfium.PdfDocument(render_pdf(doc))[0].render(scale=3).to_numpy()).astype(int)
    assert np.abs(picture(_patched(True)) - picture(_patched(False))).max() > 200
