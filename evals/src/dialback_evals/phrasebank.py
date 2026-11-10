"""The company phrase bank for the call-report fuzzer (spec 0048, B1; Q105).

Hand-written: each phrase has a known truth about whether it names the vendor (vendor, other, negation,
reseller, correction, partial), so it covers only the phrasings we thought of. A model-written bank comes
with the live agent.
"""
from __future__ import annotations

from functools import lru_cache

VENDORS = (("Acme Fasteners LLC", ("Acme Supply",)), ("Initech Parts Inc.", ()),
           ("Northwind Freight Co.", ("NW Freight",)))
OTHERS = ("Globex Logistics", "Umbrella Freight", "Hooli Supply", "Acme Fastening Co")
_SUFFIX = (" LLC", " Inc.", " Co.", ", Inc", "")


def _core(legal: str) -> str:
    """The legal name without its suffix ("Acme Fasteners")."""
    for suffix in (" LLC", " Inc.", " Co."):
        if legal.endswith(suffix):
            return legal[: -len(suffix)]
    return legal


def phrases(truth: str, legal: str, trades: tuple[str, ...], other: str = OTHERS[0]) -> tuple[str, ...]:
    """Every phrase in the bank for one truth, about one vendor."""
    name = _core(legal)
    first = name.split()[0]
    if truth == "vendor":
        return (name, legal, name.upper(), name.lower(), *(name + s for s in _SUFFIX), *trades,
                f"Uh, this is {name}", f"Yeah, {name}, accounts payable", f"{name}, how can I help?")
    if truth == "other":
        return (other, f"This is {other}", f"{other}, accounts payable")
    if truth == "negation":
        return (f"Not {name}, this is {other}", f"This isn't {name}.", f"We're not {name}, sorry.")
    if truth == "reseller":
        return (f"We're a reseller for {name}", f"{other}, we resell for {name}", f"We distribute for {name}")
    if truth == "correction":
        return (f"{name}? No, {other}", f"{name}, sorry, I mean {other}")
    if truth == "partial":
        return (first, f"{first}, I think", f"It's {first}")
    raise ValueError(truth)


def phrase(truth: str, legal: str, trades: tuple[str, ...]) -> str:
    """The bank's first phrase for a truth (the F2 company moves use it)."""
    return phrases(truth, legal, trades)[0]


@lru_cache(maxsize=1)
def _all() -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for legal, trades in VENDORS:
        for other in OTHERS:
            for truth in ("vendor", "other", "negation", "reseller", "correction", "partial"):
                for said in phrases(truth, legal, trades, other):
                    out.setdefault(said, set()).add(truth)
    return {k: frozenset(v) for k, v in out.items()}


def truths(said: str) -> frozenset[str]:
    """Every truth the bank gives this phrase, over all its vendors (empty when it isn't in the bank)."""
    return _all().get(said, frozenset())
