"""Spec 0092, D10: the known limit until Part B. A font whose outlines disagree with its character map shows one
digit and extracts another: there is ink, but not the digits the text layer holds. Part A reads the text layer, so
it reads what the map says. The plant is on the strict known-failures list (spec 0048, D3): it turns red, and comes
off the list, once Part B's pixel check lands."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from dialback_evals import known
from dialback_evals.pdfrender import FONT_PATH, render_pdf
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
    failing = _failing(render_pdf(_doc(TEXT_LAYER), font_path=swapped_font), WHAT_A_PERSON_SEES)
    entries = known.for_job(KNOWN, "lab-pdf")
    assert known.judge(entries, failing).problems == []
    assert known.missing_findings(entries, FINDINGS.read_text(encoding="utf-8")) == []


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


def test_the_list_goes_red_once_the_swap_is_caught():
    # Part B's pixel check will stop this failing; the strict list then says to take it off.
    verdict = known.judge(known.for_job(KNOWN, "lab-pdf"), {})
    assert verdict.red and "take it off the list" in verdict.problems[0]
