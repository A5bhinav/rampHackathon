"""A document's text, line by line, with whether a person could see each line.

Reads the corpus page model: spans with position, size and colour, the facts pdfplumber reports
per character. Reading real PDFs is a later spec (0016, out of scope).
"""
from __future__ import annotations

from dataclasses import dataclass

from .visibility import classify


@dataclass(frozen=True)
class Line:
    text: str
    visibility: str   # visible, white, tiny, off_page
    page: int
    index: int        # position in the document, across pages


def lines(document: dict) -> list[Line]:
    out: list[Line] = []
    for p, page in enumerate(document.get("pages") or []):
        width, height = float(page.get("width", 0)), float(page.get("height", 0))
        for span in page.get("spans") or []:
            out.append(Line(str(span.get("text", "")), classify(span, width, height), p, len(out)))
    return out


def visible_text(document_lines: list[Line]) -> str:
    """What the labeler is allowed to see: visible lines only, one per line."""
    return "\n".join(ln.text for ln in document_lines if ln.visibility == "visible")
