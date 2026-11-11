"""PDFs for the reader's tests (spec 0092). The PDFs come from the lab's renderer (evals), which the
product never imports; only these tests do."""
from __future__ import annotations

from dialback_evals.pdfrender import render_pdf, text_width
from dialback_ledger.pdf import read_pdf
from dialback_ledger.visibility import classify

__all__ = ["classes", "document_of", "pdf_of", "read_spans", "span", "text_width"]


def span(text: str, x: float = 72, y: float = 100, *, size: float = 10, color: str = "#000000", **plants) -> dict:
    return {"text": text, "x": x, "y": y, "size": size, "color": color, **plants}


def pdf_of(spans: list[dict], *, width: float = 612, height: float = 792, compress: bool = True,
           encrypt: str | None = None, pages: int = 1, **page) -> bytes:
    """A document of `pages` copies of one page holding `spans`; `page` carries renderer page keys."""
    one = {"width": width, "height": height, "spans": spans, **page}
    return render_pdf({"id": "t", "kind": "contract", "pages": [dict(one) for _ in range(pages)]},
                      compress=compress, encrypt=encrypt)


def read_spans(data: bytes) -> list[dict]:
    return [s for p in read_pdf(data) for s in p["spans"]]


def classes(data: bytes) -> list[tuple[str, str]]:
    """Each read span's text and the class ingest() will give it."""
    return [(s["text"], classify(s, p["width"], p["height"])) for p in read_pdf(data) for s in p["spans"]]


def document_of(data: bytes) -> dict:
    return {"id": "t", "kind": "contract", "pages": read_pdf(data)}


def raw_pdf(*objects: bytes) -> bytes:
    """A hand-written PDF: object k + 1 is `objects[k]`, object 1 is the catalog. For files reportlab won't
    write (no pages, a font with no way to map its glyphs to text)."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for k, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def stream(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


def unmapped_glyphs_pdf() -> bytes:
    """One line in a Type0 font (Identity-H) with no ToUnicode: nothing says which character each glyph is,
    so pdfminer reports each as "(cid:N)"."""
    return raw_pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        stream(b"BT /F1 10 Tf 72 700 Td <0014001500160017001800190020> Tj ET"),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /DialbackUnmapped /Encoding /Identity-H /DescendantFonts [6 0 R] >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /DialbackUnmapped /CIDSystemInfo << /Registry (Adobe) "
        b"/Ordering (Identity) /Supplement 0 >> /FontDescriptor 7 0 R /DW 600 >>",
        b"<< /Type /FontDescriptor /FontName /DialbackUnmapped /Flags 4 /FontBBox [0 -200 1000 800] /ItalicAngle 0 "
        b"/Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>",
    )


def no_pages_pdf() -> bytes:
    return raw_pdf(b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [] /Count 0 >>")


def refused_pdfs() -> list[tuple[str, str, bytes]]:
    """Every file D4 refuses, as (case, reason, bytes). `too_slow` isn't here: any file is too slow once the
    time limit is patched to zero."""
    from dialback_ledger.pdf import MAX_BYTES
    ar = [span("AR direct: (415) 555-0142")]
    good = pdf_of(ar)
    return [
        ("garbage", "unparseable", b"this is not a PDF"),
        ("truncated", "unparseable", good[: len(good) // 2]),
        ("encrypted", "encrypted", pdf_of(ar, encrypt="secret")),
        ("encrypted_owner_only", "encrypted", pdf_of(ar, encrypt="")),
        ("no_pages", "unparseable", no_pages_pdf()),      # PDFium won't load a document with no pages
        ("too_many_pages", "too_many_pages", pdf_of(ar, pages=21)),
        ("too_big", "too_big", good + b"%" * (MAX_BYTES + 1 - len(good))),
        ("text_annotation", "annotation", pdf_of(ar, annotations=["text"])),
        ("freetext_annotation", "annotation", pdf_of(ar, annotations=["freetext"])),
        ("form_field", "form_field", pdf_of(ar, form_field=True)),
        ("rotated", "rotated", pdf_of(ar, rotate=90)),
        ("scan", "scan", pdf_of([])),
    ]


_RENDERED: dict[str, bytes] = {}


def corpus_pdfs(documents: list[dict]) -> dict[str, bytes]:
    """Each document rendered to a PDF, by id: what `ingest_all(..., pdfs=...)` reads instead of its pages.
    Cached across tests by the document's whole content."""
    import json
    out = {}
    for d in documents:
        key = json.dumps(d, sort_keys=True)
        if key not in _RENDERED:
            _RENDERED[key] = render_pdf(d)
        out[d["id"]] = _RENDERED[key]
    return out
