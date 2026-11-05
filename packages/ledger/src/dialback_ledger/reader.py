"""The labeler: sees one document's visible text, says each value's role and party, and quotes it.

Its output is untrusted (R9) and goes through grounding.py before anything uses it. The `stub`
reader is a keyword labeler with no model (Q47). The quarantined LLM reader (`llm`) isn't built
yet, so asking for it fails closed: nothing is ingested without a reader.
"""
from __future__ import annotations

import re
from typing import Protocol

from .candidates import parse_phone
from .flags import ReaderMode


class Reader(Protocol):
    def label(self, visible_text: str) -> list: ...


def _role(label_words: list[str]) -> str:
    if "fax" in label_words:
        return "fax"
    if "billing" in label_words:
        return "billing"
    if {"receivable", "ar", "remit"} & set(label_words):
        return "ar"
    if {"main", "phone", "office"} & set(label_words):
        return "main"
    return "unknown"


class StubReader:
    """One label per visible line that holds exactly one phone; the words before the colon say the role.

    The party is the vendor only when that label names one of the vendor's own lines (main, AR,
    billing, fax); a bank's number is the bank's; anything else is unknown, which the selection
    rule refuses (R7). So text the stub can't place never counts for a number.
    """

    def label(self, visible_text: str) -> list[dict]:
        out = []
        for line in visible_text.splitlines():
            if len(parse_phone(line)) != 1:
                continue
            label_words = re.findall(r"[a-z]+", line.split(":", 1)[0].lower()) if ":" in line else []
            role = _role(label_words)
            if "bank" in re.findall(r"[a-z]+", line.lower()):
                party = "bank"
            else:
                party = "vendor" if role != "unknown" else "unknown"
            out.append({"quote": line, "role": role, "party": party})
        return out


def reader_for(mode: ReaderMode) -> Reader:
    if mode is ReaderMode.STUB:
        return StubReader()
    raise NotImplementedError("DIALBACK_READER=llm: the quarantined LLM reader isn't built yet (spec 0016); use stub")
