"""Spec 0092, D7: every page model, rendered to a real PDF and read back, comes back the same: the same text,
visibility and colour, x and y within 0.5 pt, size within 0.1 pt. The bar is exact: one wrong span fails it.
Two stand-in readers show the check can fail: one that skips the colour check (on P04a) and one that skips the
ink check (on the planted covers)."""
from __future__ import annotations

import functools

import pytest
from _sources import corpus_documents, round_trip_documents
from dialback_evals.pdfrender import TRICKS, expected_visibility, render_pdf
from dialback_ledger import legibility, pdf
from dialback_ledger.visibility import classify

SOURCES = round_trip_documents()
AR = {"text": "AR direct: (415) 555-0142", "x": 72, "y": 100, "size": 10, "color": "#000000"}
PLANTED = [(f"planted-{trick}", {"id": f"planted-{trick}", "kind": "contract", "pages": [{"width": 612, "height": 792, "spans": [
    {"text": "Main: 206.555.0117", "x": 72, "y": 86, "size": 10, "color": "#000000"}, AR | {"trick": trick}]}]})
    for trick in TRICKS]


def _key(s: dict) -> tuple[float, float]:
    return round(float(s["y"]), 1), round(float(s["x"]), 1)


def same_spans(source: dict, read_pages: list[dict]) -> list[str]:
    """Every way `read_pages` differs from `source`'s pages; [] when the round trip is exact."""
    pages = source.get("pages") or []
    if len(read_pages) != len(pages):
        return [f"{len(read_pages)} pages read, {len(pages)} rendered"]
    out = []
    for n, (want, got) in enumerate(zip(pages, read_pages), start=1):
        w, h = float(want["width"]), float(want["height"])
        if abs(got["width"] - w) > 0.5 or abs(got["height"] - h) > 0.5:
            out.append(f"page {n}: size {got['width']}x{got['height']}, rendered {w}x{h}")
        a, b = sorted(want["spans"], key=_key), sorted(got["spans"], key=_key)
        if len(a) != len(b):
            out.append(f"page {n}: {len(b)} spans read, {len(a)} rendered: {[s['text'] for s in b]}")
            continue
        for s, r in zip(a, b):
            where = f"page {n} {s['text']!r}"
            if r["text"] != s["text"]:
                out.append(f"{where}: read as {r['text']!r}")
            if r["color"] != s["color"].lower():
                out.append(f"{where}: colour {r['color']}, rendered {s['color']}")
            if abs(r["x"] - s["x"]) > 0.5 or abs(r["y"] - s["y"]) > 0.5:
                out.append(f"{where}: at ({r['x']}, {r['y']}), rendered at ({s['x']}, {s['y']})")
            if abs(r["size"] - s["size"]) > 0.1:
                out.append(f"{where}: size {r['size']}, rendered {s['size']}")
            if classify(r, w, h) != expected_visibility(s, w, h):
                out.append(f"{where}: {classify(r, w, h)}, should be {expected_visibility(s, w, h)}")
    return out


@functools.cache
def _read(source_id: str) -> tuple[dict, list[dict]]:
    doc = dict(SOURCES + PLANTED)[source_id]
    return doc, pdf.read_pdf(render_pdf(doc))


@pytest.mark.parametrize("source_id", [s for s, _ in SOURCES + PLANTED])
def test_round_trip_is_exact(source_id):
    doc, pages = _read(source_id)
    assert same_spans(doc, pages) == []


def _p04a_hidden() -> list[dict]:
    docs = [d for s, d in corpus_documents() if s.startswith("corpus-P04a-")]
    hidden = [d for d in docs if any(sp["color"] != "#000000" for p in d["pages"] for sp in p["spans"])]
    assert len(hidden) == 2, "P04a plants white text in two documents"
    return hidden


def test_the_colour_stand_in_fails_on_p04a(monkeypatch):
    monkeypatch.setattr(pdf, "_color", lambda char: "#000000")       # a reader that never reads the colour
    for doc in _p04a_hidden():
        assert same_spans(doc, pdf.read_pdf(render_pdf(doc))) != []


def test_the_ink_stand_in_fails_on_the_planted_covers(monkeypatch):
    monkeypatch.setattr(pdf, "_ink", lambda chars, need, *rest: {i: True for i in need})   # never checks ink
    for _, doc in PLANTED:
        assert same_spans(doc, pdf.read_pdf(render_pdf(doc))) != []


def test_the_real_reader_passes_where_the_stand_ins_fail():
    for doc in [*_p04a_hidden(), *(d for _, d in PLANTED)]:
        assert same_spans(doc, pdf.read_pdf(render_pdf(doc))) == []


def test_every_visible_span_has_ink_and_every_planted_one_has_none():
    # D3's two pins on the resolution and threshold: no visible span in any round-trip document comes back
    # without ink, and every planted trick does.
    lost = []
    for source_id, _ in SOURCES:
        doc, pages = _read(source_id)
        for want, got in zip(doc["pages"], pages):
            if any(expected_visibility(s, want["width"], want["height"]) == "visible" for s in want["spans"]):
                lost += [f"{source_id}: {r['text']!r}" for r in got["spans"] if "ink" in r
                         and classify(r, want["width"], want["height"]) == "no_ink"]
    assert lost == []
    for source_id, _ in PLANTED:
        _, pages = _read(source_id)
        (trick,) = [r for r in pages[0]["spans"] if r["text"] == AR["text"]]
        assert trick.get("ink") is False


def test_lab_docs_patterns_match(monkeypatch):
    # Spec 0092, D8: `lab docs` at its gate's own arguments finds the same failing patterns whether every
    # document reaches ingestion as a page model or as a real PDF read back. Both places the lab ingests (G1 to
    # G6 in docgen.check_doc, G7 through the bridge) are swapped for the PDF path. The whole report must match
    # too: with an exact round trip, ingestion's output is the same.
    from _sources import ROOT
    from dialback_evals import bridge, docgen
    from dialback_ledger import ingest as ledger_ingest
    from dialback_ledger.reader import StubReader

    frozen = ROOT / "evals" / "docs-generated"
    page_model = docgen.run(examples=1000, seed=0, reader=StubReader())
    page_model_frozen = docgen.rerun_frozen(frozen, reader=StubReader())
    # This test is about the path, over more than 10,000 documents. Tesseract's own bar is the round trip above.
    monkeypatch.setattr(legibility, "_read_matches", lambda image, expected, timeout: True)
    real = ledger_ingest.ingest_all
    calls = {"check_doc": 0, "bridge": 0}

    def through_pdfs(site):
        def ingest_all(documents, *, extractor_version, reader):
            calls[site] += 1
            return real(documents, extractor_version=extractor_version, reader=reader,
                        pdfs={d["id"]: render_pdf(d) for d in documents})
        return ingest_all

    monkeypatch.setattr(ledger_ingest, "ingest_all", through_pdfs("check_doc"))
    monkeypatch.setattr(bridge, "ingest_all", through_pdfs("bridge"))
    pdf_run = docgen.run(examples=1000, seed=0, reader=StubReader())
    pdf_frozen = docgen.rerun_frozen(frozen, reader=StubReader())
    assert calls["check_doc"] >= 1000 and calls["bridge"] >= len(docgen.draw_worlds(seed=0)), calls
    assert {v.pattern for v in pdf_run.violations} == {v.pattern for v in page_model.violations}
    assert pdf_frozen == page_model_frozen
    assert docgen.report(pdf_run) == docgen.report(page_model)
