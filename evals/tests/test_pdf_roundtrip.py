"""Spec 0092, D7: every page model, rendered to a real PDF and read back, comes back the same: the same text,
visibility and colour, x and y within 0.5 pt, size within 0.1 pt. The bar is exact: one wrong span fails it.
Two stand-in readers show the check can fail: one that skips the colour check (on P04a) and one that skips the
ink check (on the planted covers)."""
from __future__ import annotations

import functools

import pytest
from _sources import all_documents, corpus_documents
from dialback_evals.pdfrender import TRICKS, expected_visibility, render_pdf
from dialback_ledger import pdf
from dialback_ledger.visibility import classify

SOURCES = all_documents()
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
