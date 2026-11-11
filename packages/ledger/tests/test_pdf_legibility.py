"""Spec 0092, D10a: every visible phone candidate in a digital PDF must pass four tests on the page PDFium renders,
or its span is `unverified`. OCR is injected here, so each test can be made to fail alone; `real_ocr` tests run
Tesseract itself, and CI never skips them."""
from __future__ import annotations

import numpy as np
import pytest
from _pdfs import classes, corpus_pdfs, raw_page
from _world import run, world
from dialback_ledger import legibility, pdf
from dialback_ledger.custody import custody_for
from dialback_ledger.ingest import ingest_pdf
from dialback_ledger.pdf import read_pdf
from dialback_ledger.reader import ReaderUnavailable, StubReader
from dialback_ledger.visibility import classify
from test_pdf_review import UNREADABLE_IN_EVERY_RENDERER, UNREADABLE_IN_PDFIUM

AT = "72 700 Td"
N = "(4155550142)"
DIGITS = "4155550142"
PLAIN = f"BT /F1 10 Tf {AT} {N} Tj ET"
PALE = f"1 1 0.933 rg {PLAIN}"
ALL_PASS = legibility.Verdict(contrast=True, size=True, together=True, read=True)


@pytest.fixture
def ocr_says(monkeypatch):
    """OCR reading whatever the test says; records every image it was given."""
    images: list[np.ndarray] = []

    def install(digits: str = DIGITS, confidence: float = 96.0):
        def fake(image, timeout):
            images.append(image)
            return [(digits, confidence)]
        monkeypatch.setattr(legibility, "_ocr", fake)
        monkeypatch.setattr(legibility, "_read_matches", legibility.real_read_matches)
        return images

    return install


@pytest.fixture
def verdicts(monkeypatch):
    got: list[legibility.Verdict] = []
    real = legibility.assess

    def spy(*args, **kwargs):
        got.append(real(*args, **kwargs))
        return got[-1]

    monkeypatch.setattr(legibility, "assess", spy)
    return got


def test_a_plain_number_passes_all_four(ocr_says, verdicts):
    ocr_says()
    assert classes(raw_page(PLAIN)) == [(DIGITS, "visible")]
    assert verdicts == [ALL_PASS]


# --- Each test can fail on its own -----------------------------------------------------------------------

def test_only_contrast_fails_on_the_palest_yellow(ocr_says, verdicts):
    ocr_says()
    assert classes(raw_page(PALE)) == [(DIGITS, "unverified")]
    assert verdicts == [legibility.Verdict(contrast=False, size=True, together=True, read=True)]


def test_only_size_fails_when_pdfium_draws_it_tiny(ocr_says, verdicts):
    # A one-operand Tf: PDFium draws 3 points, pdfminer reports 10. Close enough together to fail size alone.
    ocr_says()
    objects = " ".join(f"BT /F1 10 Tf 3 Tf {72 + 4.5 * k} 700 Td ({d}) Tj ET" for k, d in enumerate(DIGITS))
    read_pdf(raw_page(objects))
    assert verdicts == [legibility.Verdict(contrast=True, size=False, together=True, read=True)]


def test_the_attacks_that_draw_it_tiny_fail_size(ocr_says, verdicts):
    ocr_says()
    read_pdf(UNREADABLE_IN_PDFIUM["size_differs_by_a_one_operand_tf"]())
    assert verdicts[0].size is False


def test_only_together_fails_across_a_huge_space(ocr_says, verdicts):
    ocr_says()
    read_pdf(UNREADABLE_IN_EVERY_RENDERER["joined_across_the_line_by_a_huge_space"]())
    assert [v for v in verdicts if not v] == [legibility.Verdict(contrast=True, size=True, together=False, read=True)]


@pytest.mark.parametrize("digits, confidence", [("4155550143", 96.0), ("2410555514", 96.0), (DIGITS, 1.0), ("", 96.0)],
                         ids=["other_digits", "other_order", "low_confidence", "nothing"])
def test_only_read_fails_when_ocr_disagrees(ocr_says, verdicts, digits, confidence):
    ocr_says(digits, confidence)
    assert classes(raw_page(PLAIN)) == [(DIGITS, "unverified")]
    assert verdicts == [legibility.Verdict(contrast=True, size=True, together=True, read=False)]


# --- OCR must not out-read a person ----------------------------------------------------------------------

def test_ocr_is_shown_nothing_below_the_contrast_floor(ocr_says):
    images = ocr_says()
    read_pdf(raw_page(PALE))
    assert images and int(np.ptp(images[-1])) == 0, "the digits are gone from the image OCR gets"


def test_ocr_is_shown_a_legible_number(ocr_says):
    images = ocr_says()
    read_pdf(raw_page(PLAIN))
    assert images and int(np.ptp(images[-1])) > 200


@pytest.mark.real_ocr
def test_tesseract_reads_a_plain_number():
    assert classes(raw_page(PLAIN)) == [(DIGITS, "visible")]


@pytest.mark.real_ocr
def test_the_floor_is_what_stops_tesseract_reading_the_palest_yellow(monkeypatch, verdicts):
    # The stand-in: without the floor, OCR's own binarization reads what a person can't.
    assert verdicts == [] and classes(raw_page(PALE)) == [(DIGITS, "unverified")]
    assert verdicts[-1].read is False
    monkeypatch.setattr(legibility, "_below_floor_to_background", lambda shown, background, contrast: shown)
    classes(raw_page(PALE))
    assert verdicts[-1].read is True, "without the floor, Tesseract reads it"


# --- Fail closed -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [RuntimeError("Tesseract process timeout"), OSError("broken")], ids=["timeout", "error"])
def test_an_ocr_failure_is_unverified(monkeypatch, error):
    def fails(image, timeout):
        raise error
    monkeypatch.setattr(legibility, "_ocr", fails)
    monkeypatch.setattr(legibility, "_read_matches", legibility.real_read_matches)
    assert classes(raw_page(PLAIN)) == [(DIGITS, "unverified")]


def test_a_missing_tesseract_stops_the_read(monkeypatch):
    monkeypatch.setattr(legibility, "require_ocr", legibility.real_require_ocr)
    monkeypatch.setattr(legibility.shutil, "which", lambda name: None)
    legibility.tesseract_version.cache_clear()
    with pytest.raises(ReaderUnavailable, match="[Tt]esseract"):
        read_pdf(raw_page(PLAIN))
    legibility.tesseract_version.cache_clear()


def test_ocr_time_counts_inside_the_document_limit(monkeypatch):
    # D10a: OCR is inside D4's 20 seconds. One number, and the clock has already run out.
    monkeypatch.setattr(pdf, "TIME_LIMIT_SECONDS", 0.0)
    with pytest.raises(pdf.PdfRefused) as e:
        read_pdf(raw_page(PLAIN))
    assert e.value.reason == "too_slow"


# --- What `unverified` means downstream --------------------------------------------------------------------

@pytest.mark.parametrize("value", [False, "false", 0, None])
def test_a_legible_flag_other_than_true_is_unverified(value):
    span = {"text": DIGITS, "x": 72, "y": 100, "size": 10, "color": "#000000", "legible": value}
    assert classify(span, 612, 792) == "unverified"


def test_the_earlier_classes_come_first():
    span = {"text": DIGITS, "x": 72, "y": 100, "size": 10, "color": "#000000", "legible": False, "ink": False}
    assert classify(span, 612, 792) == "no_ink"
    assert classify(span | {"color": "#ffffff"}, 612, 792) == "white"


def _doc(spans_text: str) -> dict:
    return {"id": "d1", "kind": "contract", "claimed_date": None,
            "custody": {"channel": "contract", "received_at": "2025-01-01T00:00:00+00:00", "sender": None, "thread_id": None,
                        "subject": None, "parent_document_id": None},
            "pages": [{"width": 612, "height": 792, "spans": [
                {"text": spans_text, "x": 72, "y": 100, "size": 10, "color": "#000000"}]}]}


def test_an_unverified_number_is_stored_unquoted_and_hidden_from_the_labeler(ocr_says):
    ocr_says("0000000000")
    seen: list[str] = []

    class Spy(StubReader):
        def label(self, visible_text):
            seen.append(visible_text)
            return super().label(visible_text)

    doc = _doc("AR: 415-555-0142")
    result = ingest_pdf(corpus_pdfs([doc])["d1"], doc, custody_for(doc), extractor_version="stub", reader=Spy())
    assert [(o["visibility"], o["quote"]) for o in result.observations] == [("unverified", None)]
    assert seen == [""]


def rows(visibilities: tuple[str, str]) -> list[dict]:
    return [{"e164": "+14155550142", "channel": channel, "days_before": days, "visibility": v, "role": "ar", "party": "vendor"}
            for (channel, days), v in zip((("contract", 400), ("listing", 300)), visibilities)]


def test_unverified_only_is_unconfirmed_digits_only():
    (c,) = run(world(rows(("unverified", "unverified"))))["candidates"]
    assert c["reasons"].count("unconfirmed_digits_only") == 1 and "hidden_text_only" not in c["reasons"] and not c["eligible"]


def test_unverified_beside_hidden_is_unconfirmed_digits_only():
    (c,) = run(world(rows(("unverified", "no_ink"))))["candidates"]
    assert "unconfirmed_digits_only" in c["reasons"] and "hidden_text_only" not in c["reasons"]


def test_hidden_only_is_still_hidden_text_only():
    (c,) = run(world(rows(("no_ink", "white"))))["candidates"]
    assert "hidden_text_only" in c["reasons"] and "unconfirmed_digits_only" not in c["reasons"]
