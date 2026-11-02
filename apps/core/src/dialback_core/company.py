"""Did the person name this vendor? Computed by code after the call, never by a model.

Names are compared as word sets after dropping case, punctuation and legal suffixes. It is a
match when the person said every word of the vendor's legal or trade name ("Acme Fasteners"
for "Acme Fasteners LLC"). Saying only part of it ("Acme") is not a match, so the case goes
to a person rather than trusting a partial name.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "company", "ltd", "limited",
                      "lp", "llp", "plc", "the", "and"})


def words(name: str) -> frozenset[str]:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and "))
    return frozenset(w for w in cleaned.split() if w not in SUFFIXES)


def company_matches(named: str | None, legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
    if not named or not words(named):
        return None
    said = words(named)
    return any(core and core <= said for core in (words(n) for n in (legal_name, *trade_names)))


def every_name_matches(named: Iterable[str], legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
    """Every company the person named must match (spec 0005, D7): False if any doesn't, unknown
    if none was given or any can't be read, else True."""
    results = [company_matches(n, legal_name, tuple(trade_names)) for n in named]
    if any(r is False for r in results):
        return False
    return None if not results or any(r is None for r in results) else True
