"""Did the person name this vendor? Computed by code after the call, never by a model.

The answer is the caller's whole turn, as said ("Hi, this is Acme"). It is a match (spec 0082, D6)
when either:

- it holds every word of the vendor's legal or trade name, in any order, after dropping case,
  punctuation and legal suffixes ("Fasteners, Acme" for "Acme Fasteners LLC"); or
- one of the ways the speech guard says the vendor's name (`vendor_variants`: the name without its
  suffix, run together, its first two words, or a distinctive first word) appears in it as a run of
  whole words ("This is Acme", "AcmeFasteners").

Nothing is fuzzy: "acne" is not "Acme" (D8). Anything else is not a match, and the case goes to a
person; an answer with no readable words is unknown.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from dialback_policy.guard import clean, vendor_variants

SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "company", "ltd", "limited",
                      "lp", "llp", "plc", "the", "and"})


def words(name: str) -> frozenset[str]:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", name.lower().replace("&", " and "))
    return frozenset(w for w in cleaned.split() if w not in SUFFIXES)


def _says_a_variant(named: str, names: tuple[str, ...]) -> bool:
    """One of the vendor's name variants is in `named` as a run of whole words, read as the guard reads text."""
    said = re.findall(r"[a-z0-9]+", clean(named))
    for variant in vendor_variants(names):
        run = variant.split()
        if run and any(said[i:i + len(run)] == run for i in range(len(said) - len(run) + 1)):
            return True
    return False


def company_matches(named: str | None, legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
    if not named:
        return None
    names = (legal_name, *trade_names)
    said = words(named)
    if said and any(core and core <= said for core in (words(n) for n in names)):
        return True
    if _says_a_variant(named, names):
        return True
    return False if said else None


def every_name_matches(named: Iterable[str], legal_name: str, trade_names: Iterable[str] = ()) -> bool | None:
    """Every company the person named must match (spec 0005, D7): False if any doesn't, unknown
    if none was given or any can't be read, else True."""
    results = [company_matches(n, legal_name, tuple(trade_names)) for n in named]
    if any(r is False for r in results):
        return False
    return None if not results or any(r is None for r in results) else True
