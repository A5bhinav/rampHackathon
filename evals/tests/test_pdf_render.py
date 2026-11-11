"""Spec 0092, D6: the lab renderer writes any page model to a real PDF (reportlab, an embedded font)."""
from __future__ import annotations

import io
import shutil

import pdfplumber
import pytest
from _sources import ROOT, all_documents, bank_strings, corpus_documents
from dialback_evals import pdfrender
from dialback_evals.cli import main
from dialback_evals.pdfrender import RenderError, overlaps, render_pdf

TEAM = "+12065550188"   # fictional here; on the day, the operator types the team phone


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


# --- The demo's PDFs (D9): lab pdf render --------------------------------------------------------------

def _copy_of_committed(tmp_path, monkeypatch):
    folder = tmp_path / "demo_pdfs"
    shutil.copytree(pdfrender.DEMO_PDFS, folder)
    monkeypatch.setattr(pdfrender, "DEMO_PDFS", folder)
    return folder


def test_check_passes_on_the_committed_pdfs(capsys):
    assert main(["pdf", "render", "--check"]) == 0, capsys.readouterr().out


def test_check_fails_on_a_stale_pdf(tmp_path, monkeypatch, capsys):
    folder = _copy_of_committed(tmp_path, monkeypatch)
    stale = min(folder.glob("*.pdf"))
    data = bytearray(stale.read_bytes())
    data[len(data) // 2] ^= 1
    stale.write_bytes(bytes(data))
    assert main(["pdf", "render", "--check"]) == 1
    assert stale.name in capsys.readouterr().out


def test_check_fails_on_a_missing_pdf(tmp_path, monkeypatch, capsys):
    folder = _copy_of_committed(tmp_path, monkeypatch)
    gone = min(folder.glob("*.pdf"))
    gone.unlink()
    assert main(["pdf", "render", "--check"]) == 1
    assert gone.name in capsys.readouterr().out


def test_check_fails_on_an_extra_pdf(tmp_path, monkeypatch, capsys):
    folder = _copy_of_committed(tmp_path, monkeypatch)
    (folder / "extra.pdf").write_bytes(b"%PDF-1.7\n")
    assert main(["pdf", "render", "--check"]) == 1
    assert "extra.pdf" in capsys.readouterr().out


def test_render_writes_the_folder_and_removes_what_isnt_ours(tmp_path, monkeypatch):
    folder = _copy_of_committed(tmp_path, monkeypatch)
    (folder / "extra.pdf").write_bytes(b"%PDF-1.7\n")
    sorted(folder.glob("*.pdf"))[1].unlink()
    assert main(["pdf", "render"]) == 0
    assert main(["pdf", "render", "--check"]) == 0


def test_the_committed_pdfs_hold_no_compressed_stream():
    # Their bytes are compared across machines (--check runs in CI), and zlib's output can differ between them.
    assert not [p.name for p in pdfrender.DEMO_PDFS.glob("*.pdf") if b"FlateDecode" in p.read_bytes()]


def test_phone_needs_an_out_dir_outside_the_repo(tmp_path, capsys):
    assert main(["pdf", "render", "--demo", "--phone", f"acme={TEAM}"]) == 2
    assert "--out" in capsys.readouterr().out
    inside = ROOT / "evals" / "out" / "team-pdfs"
    assert main(["pdf", "render", "--demo", "--phone", f"acme={TEAM}", "--out", str(inside)]) == 2
    assert "never committed" in capsys.readouterr().out
    assert not inside.exists()


def test_check_refuses_an_out_or_a_phone(tmp_path, capsys):
    assert main(["pdf", "render", "--check", "--out", str(tmp_path / "x")]) == 2
    assert main(["pdf", "render", "--check", "--phone", f"acme={TEAM}"]) == 2


def test_a_bad_phone_is_refused(tmp_path, capsys):
    assert main(["pdf", "render", "--phone", "acme=not-a-phone", "--out", str(tmp_path / "x")]) == 2
    assert main(["pdf", "render", "--phone", f"nobody={TEAM}", "--out", str(tmp_path / "y")]) == 2


def test_phone_is_rendered_into_the_pdfs(tmp_path):
    from dialback_ledger.candidates import find_phones
    from dialback_ledger.demo import load_seed
    from dialback_ledger.pdf import read_pdf
    from dialback_ledger.text import lines
    assert main(["pdf", "render", "--demo", "--phone", f"acme={TEAM}", "--out", str(tmp_path)]) == 0
    acme = load_seed()["vendors"]["acme"]
    found = {c.e164 for d in acme["documents"]
             for c in find_phones(lines({"pages": read_pdf((tmp_path / f"{d['id']}.pdf").read_bytes())}))}
    assert TEAM in found and acme["phones"]["ar"] not in found
