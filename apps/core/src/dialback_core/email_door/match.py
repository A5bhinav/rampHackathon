"""Which known vendor is this email about? (design section 5). Pure code, no database.

In order: the sender's domain is one of a vendor's domains; it is a lookalike of one; a vendor's
name appears in the subject or text. The first step with any match decides, and more than one vendor
at that step is `several` (no match). Names use `company.words`' normalization (case, punctuation,
legal suffixes dropped) and need every remaining word, together and in order.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from dialback_ledger.lookalike import is_lookalike, sender_domain

from dialback_core.company import SUFFIXES

_BOTH_SIDES = frozenset({"and", "the"})  # dropped from the text as well as the name
from dialback_core.email_door.rules import MAX_CHARS

LOOKALIKE_DOMAIN = "lookalike_domain"
SENDER_OFF_DOMAIN = "sender_off_domain"

_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u02bc": "'",
                         "\u201c": '"', "\u201d": '"', "\u201e": '"'})


@dataclass(frozen=True)
class VendorRef:
    vendor_id: UUID
    legal_name: str
    trade_names: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True)
class Match:
    vendor_id: UUID | None
    how: Literal["domain", "lookalike", "name", "none", "several"]
    red_flags: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    """Lowercase words after folding accents and smart quotes; `&` is `and` (as company.words)."""
    folded = unicodedata.normalize("NFKD", text.translate(_QUOTES))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", folded.lower().replace("&", " and ")).split()


def _contains(haystack: list[str], needle: list[str]) -> bool:
    """Linear (Knuth-Morris-Pratt) search for `needle` as a run of `haystack`."""
    fail = [0] * len(needle)
    k = 0
    for i in range(1, len(needle)):
        while k and needle[i] != needle[k]:
            k = fail[k - 1]
        k += needle[i] == needle[k]
        fail[i] = k
    k = 0
    for token in haystack:
        while k and token != needle[k]:
            k = fail[k - 1]
        k += token == needle[k]
        if k == len(needle):
            return True
    return False


def name_in_text(name: str, text: str) -> bool:
    """True when the name's words, legal suffixes dropped, appear together and in order in the first
    MAX_CHARS characters of `text`. A name with no words left never matches."""
    wanted = [t for t in _tokens(name) if t not in SUFFIXES]  # "and"/"the" are in SUFFIXES
    return bool(wanted) and _contains([t for t in _tokens(text[:MAX_CHARS]) if t not in _BOTH_SIDES], wanted)


def match_vendor(sender: str, subject: str, text: str, vendors: Sequence[VendorRef]) -> Match:
    domain = sender_domain(sender)

    def decide(found: list[VendorRef], how: Literal["domain", "lookalike", "name"],
               flags: tuple[str, ...]) -> Match | None:
        ids = {v.vendor_id for v in found}
        if not ids:
            return None
        if len(ids) > 1:
            return Match(None, "several", ())
        return Match(next(iter(ids)), how, flags)

    if domain:
        known = domain.rstrip(".")
        hit = decide([v for v in vendors if any(
            known == k or known.endswith("." + k)
            for k in (d.strip().lower().rstrip(".") for d in v.domains) if k)], "domain", ())
        if hit:
            return hit
        hit = decide([v for v in vendors if is_lookalike(known, v.domains)], "lookalike", (LOOKALIKE_DOMAIN,))
        if hit:
            return hit
    hit = decide([v for v in vendors
                  if any(name_in_text(n, (subject or "")[:MAX_CHARS]) or name_in_text(n, text or "")
                         for n in (v.legal_name, *v.trade_names))], "name", (SENDER_OFF_DOMAIN,))
    return hit or Match(None, "none", ())
