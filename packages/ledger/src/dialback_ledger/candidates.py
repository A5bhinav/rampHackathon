"""Phone numbers in a document, found by code (libphonenumber, strict), never by a model."""
from __future__ import annotations

from dataclasses import dataclass

import phonenumbers as pn

from .text import Line

REGION = "US"


@dataclass(frozen=True)
class Candidate:
    e164: str
    line: int
    start: int
    end: int
    raw: str
    visibility: str


def parse_phone(text: str) -> list[str]:
    """Every valid phone number in `text`, as E.164, in order."""
    return [pn.format_number(m.number, pn.PhoneNumberFormat.E164)
            for m in pn.PhoneNumberMatcher(text, REGION, leniency=pn.Leniency.VALID)]


def find_phones(document_lines: list[Line]) -> list[Candidate]:
    out = []
    for ln in document_lines:
        for m in pn.PhoneNumberMatcher(ln.text, REGION, leniency=pn.Leniency.VALID):
            out.append(Candidate(pn.format_number(m.number, pn.PhoneNumberFormat.E164), ln.index, m.start, m.end,
                                 m.raw_string, ln.visibility))
    return out
