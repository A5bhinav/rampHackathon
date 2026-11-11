"""Spec 0092, Part A: the PDF reader. A real PDF reads back into the page model, with visibility decided per
character, so ingest() and everything after it is unchanged."""
from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from _pdfs import (
    classes,
    corpus_pdfs,
    document_of,
    pdf_of,
    read_spans,
    refused_pdfs,
    span,
    text_width,
    unmapped_glyphs_pdf,
)
from _world import CASE, DATASET, VENDOR
from dialback_ledger.candidates import find_phones
from dialback_ledger.custody import custody_for
from dialback_ledger.ingest import ingest_all, ingest_pdf
from dialback_ledger.pdf import PdfRefused, read_pdf
from dialback_ledger.reader import StubReader
from dialback_ledger.select import ChangeRequest, DocumentRef, Sighting, select
from dialback_ledger.text import lines
from dialback_ledger.visibility import classify


def only(data: bytes) -> tuple[str, str]:
    (got,) = classes(data)
    return got


# --- The page model comes back ------------------------------------------------------------------------

def test_visible_text_reads_back():
    (s,) = read_spans(pdf_of([span("Main: 206.555.0117", 72, 86, color="#123456")]))
    assert s["text"] == "Main: 206.555.0117"
    assert s["x"] == pytest.approx(72, abs=0.5) and s["y"] == pytest.approx(86, abs=0.5)
    assert s["size"] == pytest.approx(10, abs=0.1) and s["color"] == "#123456"
    assert classify(s, 612, 792) == "visible"


def test_cells_on_one_baseline_are_separate_spans():
    got = read_spans(pdf_of([span("Accounts receivable", 72), span("(415) 555-0142", 320)]))
    assert [(s["text"], round(s["x"])) for s in got] == [("Accounts receivable", 72), ("(415) 555-0142", 320)]


# --- Colour (D3: white) ----------------------------------------------------------------------------------

def test_white_is_white():
    assert only(pdf_of([span("AR direct: (206) 555-0169", color="#ffffff")])) == ("AR direct: (206) 555-0169", "white")


def test_near_white_is_white():
    assert only(pdf_of([span("AR direct: (206) 555-0169", color="#f4f4f4")]))[1] == "white"


def test_near_white_gray_is_white():
    assert only(pdf_of([span("AR direct: (206) 555-0169", color_space="gray", color_value=[0.957])]))[1] == "white"


def test_near_white_cmyk_is_white():
    assert only(pdf_of([span("AR direct: (206) 555-0169", color_space="cmyk", color_value=[0.04, 0.04, 0.04, 0])]))[1] == "white"


def test_unconvertible_colour_is_white():
    # A spot colour (Separation): the reader can't say what a person sees, so it counts as white (R7).
    assert only(pdf_of([span("AR direct: (206) 555-0169", color_space="separation", color_value=[1, 1, 1, 1])]))[1] == "white"


# --- Size and position (D3: tiny, off_page) --------------------------------------------------------------

def test_tiny_is_tiny():
    assert only(pdf_of([span("AR direct: 617.555.0169", size=3.9)]))[1] == "tiny"


@pytest.mark.parametrize("x, y", [(-200, 100), (702, 100), (72, -20), (72, 800)], ids=["left", "right", "top", "bottom"])
def test_off_page_past_each_edge_is_off_page(x, y):
    assert only(pdf_of([span("AR direct: 303-555-0169", x, y)]))[1] == "off_page"


def test_a_span_straddling_the_edge_is_hidden():
    # Its origin is on the page, its last digits aren't: a person sees part of a number at most.
    got = classes(pdf_of([span("AR 415-555-0142", 600)]))
    assert [c for t, c in got if any(ch.isdigit() for ch in t) and c == "visible"] == []
    assert got, "the text is read, and kept as hidden"


def test_text_outside_the_crop_box_is_off_page():
    # On the media box, below the crop box: a viewer never shows it.
    data = pdf_of([span("Notices: 415-555-0142", 72, 760), span("Main: 206.555.0117", 72, 100)], crop=[50, 60, 562, 742])
    (page,) = document_of(data)["pages"]
    assert (page["width"], page["height"]) == (512, 682)
    by_text = {s["text"]: s for s in page["spans"]}
    assert classify(by_text["Notices: 415-555-0142"], 512, 682) == "off_page"
    inside = by_text["Main: 206.555.0117"]
    assert (round(inside["x"], 1), round(inside["y"], 1)) == (22.0, 40.0)
    assert classify(inside, 512, 682) == "visible"


def test_squashed_text_is_no_ink():
    # 10 points tall, a smudge wide: horizontal scaling of 5% (the Tz operator). The page model has no width, so
    # the reader can only say it is drawn so no one can read it: no_ink.
    assert only(pdf_of([span("AR direct: 415-555-0142", hscale=5)]))[1] == "no_ink"


def test_rotated_text_is_hidden():
    # The line model can't say what a person reads in rotated text, so none of it is visible: no_ink, or tiny
    # where the rotated glyph measures under 4 points tall (D3's order puts tiny first).
    got = classes(pdf_of([span("AR 415-555-0142", 300, 300, rotate=90)]))
    assert got and {c for t, c in got if t.strip()} <= {"no_ink", "tiny"}
    assert "no_ink" in {c for _, c in got}


# --- The page-model flag (D1) ----------------------------------------------------------------------------

@pytest.mark.parametrize("value", [False, "false", 0, None])
def test_an_ink_flag_other_than_true_is_no_ink(value):
    assert classify(span("AR: 415-555-0142", ink=value), 612, 792) == "no_ink"


def test_an_ink_flag_of_true_changes_nothing():
    assert classify(span("AR: 415-555-0142", ink=True), 612, 792) == "visible"
    assert classify(span("AR: 415-555-0142", color="#ffffff", ink=False), 612, 792) == "white"   # the earlier classes first


# --- What a hidden character does to a number (D2) ---------------------------------------------------

def test_a_hidden_digit_inside_a_visible_number_gives_no_candidate():
    head = "AR: (415) 555-01"
    x6 = 72 + text_width(head, 10)
    data = pdf_of([span(head), span("6", x6, color="#ffffff"), span("42", x6 + text_width("6", 10))])
    doc = document_of(data)
    assert [c.e164 for c in find_phones(lines(doc))] == []
    assert [(t, c) for t, c in classes(data)] == [(head, "visible"), ("6", "white"), ("42", "visible")]


# --- Glyphs the text layer can't map (D4) ----------------------------------------------------------------

def test_unmapped_glyphs_hold_no_digits():
    # pdfminer reads each glyph as "(cid:20)" and so on: digits that aren't on the page.
    data = unmapped_glyphs_pdf()
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        assert all(c["text"].startswith("(cid:") for c in pdf.pages[0].chars)
    got = read_spans(data)
    assert got and len("".join(s["text"] for s in got)) == 7, "each glyph is kept, as one character"
    assert not any(ch.isdigit() for s in got for ch in s["text"])


# --- Ink (D3): with and without the text that draws it -------------------------------------------------

AR = "AR direct: (415) 555-0142"


def no_visible_candidate(data: bytes, e164: str = "+14155550142") -> bool:
    return not [c for c in find_phones(lines(document_of(data))) if c.e164 == e164 and c.visibility == "visible"]


@pytest.mark.parametrize("plant", [{"trick": "cover_rect"}, {"trick": "cover_image"}, {"trick": "invisible"},
                                   {"trick": "invisible", "under": "#000000"}, {"trick": "clipped"}],
                         ids=["covered_by_a_rectangle", "covered_by_an_image", "invisible_render_mode",
                              "invisible_over_a_black_bar", "clipped"])
def test_the_ink_tricks_are_no_ink(plant):
    data = pdf_of([span("Main: 206.555.0117", y=86), span(AR, **plant)])
    assert classes(data) == [("Main: 206.555.0117", "visible"), (AR, "no_ink")]
    assert no_visible_candidate(data)


def test_covered_by_a_rectangle_is_no_ink():
    assert only(pdf_of([span(AR, trick="cover_rect")])) == (AR, "no_ink")


def test_covered_by_an_image_is_no_ink():
    assert only(pdf_of([span(AR, trick="cover_image")])) == (AR, "no_ink")


def test_invisible_render_mode_is_no_ink():
    assert only(pdf_of([span(AR, trick="invisible")])) == (AR, "no_ink")


def test_invisible_over_a_black_bar_is_no_ink():
    assert only(pdf_of([span(AR, trick="invisible", under="#000000")])) == (AR, "no_ink")


def test_clipped_is_no_ink():
    assert only(pdf_of([span(AR, trick="clipped")])) == (AR, "no_ink")


def test_an_invisible_number_overlapping_a_visible_line_is_no_ink():
    visible = span("Main: 206.555.0117", 72, 100)
    hidden = span("415-555-0109", 72 + text_width("Main: ", 10), 100, trick="invisible")
    data = pdf_of([visible, hidden])
    assert no_visible_candidate(data, "+14155550109")
    seen = "".join(t for t, c in classes(data) if c == "visible")
    assert sorted(seen.replace(" ", "")) == sorted("Main:206.555.0117"), "only the visible line's characters are visible"


def test_white_over_a_white_shape_is_white():
    assert only(pdf_of([span(AR, color="#ffffff", under="#ffffff")])) == (AR, "white")


def test_a_colour_over_a_shape_of_that_colour_is_no_ink():
    assert only(pdf_of([span(AR, color="#808080", under="#808080")])) == (AR, "no_ink")


def test_spaces_keep_a_no_ink_span_whole():
    data = pdf_of([span(AR, trick="invisible")])
    assert only(data) == (AR, "no_ink")
    assert [(c.e164, c.visibility) for c in find_phones(lines(document_of(data)))] == [("+14155550142", "no_ink")]


def test_light_but_visible_text_has_ink():
    # Words, not a number: this pins D3's ink rule, and D10a would judge a number's contrast separately.
    assert only(pdf_of([span("Accounts receivable", color="#e6e6e6")])) == ("Accounts receivable", "visible")


# --- What the reader refuses (D4) ----------------------------------------------------------------------

REFUSED = refused_pdfs()


@pytest.mark.parametrize("reason, data", [(r, d) for _, r, d in REFUSED], ids=[c for c, _, _ in REFUSED])
def test_refuses(reason, data):
    with pytest.raises(PdfRefused) as e:
        read_pdf(data)
    assert e.value.reason == reason
    assert "415" not in str(e.value), "a refusal never shows document text"


@pytest.mark.parametrize("case, reason", [("garbage", "unparseable"), ("truncated", "unparseable"), ("no_pages", "unparseable"),
                                          ("encrypted", "encrypted"), ("encrypted_owner_only", "encrypted")])
def test_refuses_by_name(case, reason):
    with pytest.raises(PdfRefused) as e:
        read_pdf({c: d for c, _, d in REFUSED}[case])
    assert e.value.reason == reason


def test_refuses_too_big_before_parsing(monkeypatch):
    from dialback_ledger import pdf
    monkeypatch.setattr(pdf.pdfium, "PdfDocument", lambda *a, **k: pytest.fail("parsed a file over the size limit"))
    with pytest.raises(PdfRefused) as e:
        read_pdf(b"%" * (pdf.MAX_BYTES + 1))
    assert e.value.reason == "too_big"


def test_refuses_too_slow(monkeypatch):
    from dialback_ledger import pdf
    monkeypatch.setattr(pdf, "TIME_LIMIT_SECONDS", 0.0)
    with pytest.raises(PdfRefused) as e:
        read_pdf(pdf_of([span(AR)]))
    assert e.value.reason == "too_slow"


def test_twenty_pages_are_read():
    assert len(read_pdf(pdf_of([span(AR)], pages=20))) == 20


def test_a_link_is_read():
    assert only(pdf_of([span(AR)], annotations=["link"])) == (AR, "visible")


def test_the_limits_are_the_specs():
    from dialback_ledger import pdf
    assert (pdf.MAX_PAGES, pdf.MAX_BYTES, pdf.TIME_LIMIT_SECONDS) == (20, 10 * 1024 * 1024, 20.0)


# --- Into ingestion (D1, D5): ingest_pdf ----------------------------------------------------------------

T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)


def _doc(doc_id: str, channel: str, days: int, spans: list[dict], *, sender=None, thread=None, subject=None) -> dict:
    return {"id": doc_id, "kind": {"email": "email", "listing": "listing"}.get(channel, "contract"),
            "custody": {"channel": channel, "received_at": (T0 - dt.timedelta(days=days)).isoformat(), "sender": sender,
                        "thread_id": thread, "subject": subject, "parent_document_id": None},
            "claimed_date": None, "pages": [{"width": 612, "height": 792, "spans": spans}]}


def _vendor_world(**ar_plants) -> list[dict]:
    """The vendor's AR line in a contract and a listing long before the request, then the request itself."""
    ar = [span("Main: 206.555.0117", y=86), span("Accounts receivable: (415) 555-0142", **ar_plants)]
    return [_doc("contract", "contract", 400, ar), _doc("listing", "listing", 300, ar),
            _doc("request", "email", 0, [span("Please update our bank details.")], sender="ar@acme.example",
                 thread="req", subject="Updated banking details")]


def _select(docs: list[dict]) -> dict:
    result = ingest_all(docs, extractor_version="stub", reader=StubReader(), pdfs=corpus_pdfs(docs))
    refs = [DocumentRef(d["id"], d["custody_channel"], d["received_at"], d["sender"], d["thread_id"], d["subject"],
                        d["received_at"]) for d in result.documents]
    sightings = [Sighting(o["value_normalized"], o["document_id"], o["source_type"], o["seen_at"], o["visibility"],
                          o["role"], o["party"], o["seen_at"]) for o in result.observations]
    return select(case_id=CASE, dataset_id=DATASET, vendor_id=VENDOR, vendor_domains=("acme.example",),
                  request=ChangeRequest("request", T0, "req", "Updated banking details", "ar@acme.example"),
                  requested_cutoff=T0 + dt.timedelta(hours=2), case_opened_at=T0 + dt.timedelta(hours=3),
                  documents=refs, sightings=sightings)


def test_a_number_seen_only_in_no_ink_text_is_refused_with_hidden_text_only():
    by = {c["e164"]: c for c in _select(_vendor_world(trick="cover_rect"))["candidates"]}
    assert "hidden_text_only" in by["+14155550142"]["reasons"] and not by["+14155550142"]["eligible"]


def test_the_same_number_in_visible_text_is_eligible():
    by = {c["e164"]: c for c in _select(_vendor_world())["candidates"]}
    assert by["+14155550142"]["eligible"], by["+14155550142"]["reasons"]


def test_ingest_pdf_hashes_the_file_and_names_both_versions():
    (doc, *_) = _vendor_world()
    data = corpus_pdfs([doc])[doc["id"]]
    result = ingest_pdf(data, doc, custody_for(doc), extractor_version="stub", reader=StubReader())
    assert result.documents[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert result.observations and {o["extractor_version"] for o in result.observations} == {"pdf-1/stub"}


def _refusing(monkeypatch, case: str) -> bytes:
    if case == "too_slow":
        from dialback_ledger import pdf
        monkeypatch.setattr(pdf, "TIME_LIMIT_SECONDS", 0.0)
        return pdf_of([span(AR)])
    return {c: d for c, _, d in REFUSED}[case]


@pytest.mark.parametrize("case", [c for c, _, _ in REFUSED] + ["too_slow"])
def test_a_refused_pdf_stores_nothing(monkeypatch, case):
    (doc, listing, _) = _vendor_world()
    result = ingest_pdf(corpus_pdfs([listing])[listing["id"]], listing, custody_for(listing), extractor_version="stub",
                        reader=StubReader())
    before = (list(result.documents), list(result.observations), list(result.dropped))
    with pytest.raises(PdfRefused) as e:
        ingest_pdf(_refusing(monkeypatch, case), doc, custody_for(doc), extractor_version="stub", reader=StubReader(),
                   result=result)
    assert e.value.document_id == doc["id"]
    assert (result.documents, result.observations, result.dropped) == before


def test_a_refused_pdf_stops_the_batch():
    docs = _vendor_world()
    pdfs = corpus_pdfs(docs) | {"listing": b"not a PDF"}
    with pytest.raises(PdfRefused) as e:
        ingest_all(docs, extractor_version="stub", reader=StubReader(), pdfs=pdfs)
    assert (e.value.document_id, e.value.reason) == ("listing", "unparseable")


# --- dialback-ledger read-pdf FILE (D9) ---------------------------------------------------------------

def test_read_pdf_command_prints_lines_and_candidates(tmp_path, capsys, monkeypatch):
    from dialback_ledger.demo import main
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "doc.pdf"
    path.write_bytes(pdf_of([span("Main: 206.555.0117", y=86), span(AR, trick="invisible")]))
    assert main(["read-pdf", str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["1 visible  Main: 206.555.0117", f"1 no_ink   {AR}"]
    assert out[2:] == ["phones:", "  +12065550117 visible", "  +14155550142 no_ink"]
    path.write_bytes(b"not a PDF")
    assert main(["read-pdf", str(path)]) == 2
    assert capsys.readouterr().err.strip() == "refused: unparseable"
