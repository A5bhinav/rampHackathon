"""Grounding (design §4, OWASP LLM09): a label survives only if code can stand behind it.

Its quote must appear verbatim in a visible line and hold exactly one phone number, which code
parses from the quote and which must be a visible candidate at that place. The value always comes
from the quote, never from the labeler. Everything else is dropped with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from .candidates import Candidate, parse_phone
from .text import Line

ROLES = {"main", "ar", "billing", "fax"}
PARTIES = {"vendor", "customer", "bank"}


@dataclass(frozen=True)
class Grounded:
    candidate: Candidate
    role: str
    party: str
    quote: str


def ground(labels: object, document_lines: list[Line], candidates: list[Candidate]) -> tuple[list[Grounded], list]:
    visible = [ln for ln in document_lines if ln.visibility == "visible"]
    found: dict[Candidate, list[tuple[str, str, str]]] = {}
    dropped = []
    for raw in labels if isinstance(labels, list) else [labels]:
        quote = raw.get("quote") if isinstance(raw, dict) else None
        if not isinstance(quote, str) or not quote.strip():
            dropped.append((raw, "no quote"))
            continue
        values = parse_phone(quote)
        if len(values) != 1:
            dropped.append((raw, "the quote must hold exactly one phone number"))
            continue
        hits = [c for ln in visible for q0 in _offsets(ln.text, quote) for c in candidates
                if c.line == ln.index and c.e164 == values[0] and q0 <= c.start and c.end <= q0 + len(quote)]
        if not hits:
            dropped.append((raw, "the quote isn't in the visible text at a candidate"))
            continue
        role = raw.get("role") if raw.get("role") in ROLES else "unknown"
        party = raw.get("party") if raw.get("party") in PARTIES else "unknown"
        for c in hits:
            found.setdefault(c, []).append((role, party, quote))
    accepted = []
    for c, labels_for_c in found.items():
        roles, parties = {r for r, _, _ in labels_for_c}, {p for _, p, _ in labels_for_c}
        accepted.append(Grounded(c, roles.pop() if len(roles) == 1 else "unknown",
                                 parties.pop() if len(parties) == 1 else "unknown", labels_for_c[0][2]))
    return accepted, dropped


def _offsets(text: str, quote: str) -> list[int]:
    out, i = [], text.find(quote)
    while i != -1:
        out.append(i)
        i = text.find(quote, i + 1)
    return out
