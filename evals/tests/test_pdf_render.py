"""Spec 0092, D6: the lab renderer writes any page model to a real PDF (reportlab, an embedded font)."""
from __future__ import annotations

import io

import pdfplumber
import pytest
from _sources import all_documents, bank_strings, corpus_documents
from dialback_evals.pdfrender import RenderError, overlaps, render_pdf


def _doc(*spans: dict, doc_id: str = "t1") -> dict:
    return {"id": doc_id, "kind": "invoice", "pages": [{"width": 612, "height": 792, "spans": list(spans)}]}


def test_render_is_deterministic():
    _, doc = next(corpus_documents())
    assert render_pdf(doc) == render_pdf(doc)


def test_a_character_the_font_cant_draw_fails_the_render():
    with pytest.raises(RenderError, match="U\\+4E00"):
        render_pdf(_doc({"text": "Call \u4e00", "x": 72, "y": 72, "size": 10, "color": "#000000"}))


def test_the_font_covers_every_source_character():
    chars = {c for _, d in all_documents() for p in d["pages"] for s in p["spans"] for c in s["text"]}
    chars |= {c for s in bank_strings() for c in s}
    line = "".join(sorted(chars))
    render_pdf(_doc(*({"text": line[i:i + 60], "x": 10, "y": 20 + 12 * k, "size": 10, "color": "#000000"}
                      for k, i in enumerate(range(0, len(line), 60)))))


def test_no_source_has_overlapping_spans():
    found = [line for _, d in all_documents() for line in overlaps(d)]
    assert found == []


def test_overlaps_names_the_page():
    span = {"text": "AR: 415-555-0142", "x": 72, "y": 100, "size": 10, "color": "#000000"}
    assert overlaps(_doc(span, dict(span), doc_id="d9")) == ["d9 page 1: 'AR: 415-555-0142' and 'AR: 415-555-0142'"]


def test_pdfplumber_reads_the_page_model_back():
    data = render_pdf(_doc({"text": "Main: 206.555.0117", "x": 72, "y": 86, "size": 10, "color": "#123456"}))
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first = pdf.pages[0].chars[0]
    assert first["text"] == "M"
    assert first["x0"] == pytest.approx(72, abs=0.01)
    assert first["top"] == pytest.approx(86, abs=0.01)
    assert first["size"] == pytest.approx(10)
    assert first["non_stroking_color"] == pytest.approx((0x12 / 255, 0x34 / 255, 0x56 / 255), abs=0.002)


def test_unknown_trick_fails_the_render():
    with pytest.raises(RenderError, match="glow"):
        render_pdf(_doc({"text": "AR: 415-555-0142", "x": 72, "y": 72, "size": 10, "color": "#000000", "trick": "glow"}))
