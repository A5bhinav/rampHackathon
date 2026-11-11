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


# Each phrase class, and its truth: does the answer name the vendor? (Spec 0048 review fix 5 adds the near
# variants production's word-subset check gets wrong: a singular, a typo, another company whose name holds
# the vendor's, and a past connection.)
CLASSES = {
    "vendor": "vendor", "vendor_filler": "vendor", "vendor_trade": "vendor", "vendor_singular": "vendor",
    "vendor_typo": "vendor",
    "other": "other", "other_containing_vendor": "other", "past_connection": "other",
    "negation": "negation", "reseller": "reseller", "correction": "correction", "partial": "partial",
}


# Spec 0082, D7a: a refusal says the person is not with the vendor, or only works with it. Every phrase in these
# classes is one. A correction is one only in its "I mean" form; "Acme? No, Globex" holds no refusal word (D9).
REFUSAL_CLASSES = ("negation", "reseller", "past_connection")


def refuses(cls: str, said: str) -> bool:
    """The generator's truth: does this answer, of phrase class `cls`, refuse (spec 0082, D7a)?"""
    return cls in REFUSAL_CLASSES or (cls == "correction" and ", sorry, I mean " in said)


def _singular(name: str) -> str | None:
    words = name.split()
    return " ".join([*words[:-1], words[-1][:-1]]) if words[-1].endswith("s") else None


def _typo(name: str) -> str:
    first, *rest = name.split()
    wrong = first.replace("c", "ck", 1) if "c" in first else first[:2] + first[1:]
    return " ".join([wrong, *rest])


def phrases_of(cls: str, legal: str, trades: tuple[str, ...], other: str = OTHERS[0]) -> tuple[str, ...]:
    """Every phrase in one class, about one vendor (empty when the class has none for it)."""
    name = _core(legal)
    first = name.split()[0]
    table = {
        "vendor": (name, legal, name.upper(), name.lower(), *(name + s for s in _SUFFIX)),
        "vendor_filler": (f"Uh, this is {name}", f"Yeah, {name}, accounts payable", f"{name}, how can I help?"),
        "vendor_trade": tuple(trades),
        "vendor_singular": tuple(x for x in (_singular(name),) if x),
        "vendor_typo": (_typo(name),),
        "other": (other, f"This is {other}", f"{other}, accounts payable"),
        "other_containing_vendor": (f"{name} Supply Co", f"{name} Holdings", f"This is {name} Logistics"),
        "past_connection": (f"I used to work at {name}", f"I was at {name} until last year"),
        "negation": (f"Not {name}, this is {other}", f"This isn't {name}.", f"We're not {name}, sorry.",
                     f"{name}? Never heard of them"),
        "reseller": (f"We're a reseller for {name}", f"{other}, we resell for {name}", f"We distribute for {name}"),
        "correction": (f"{name}? No, {other}", f"{name}, sorry, I mean {other}"),
        "partial": (first, f"{first}, I think", f"It's {first}"),
    }
    return tuple(dict.fromkeys(table[cls]))


def phrases(truth: str, legal: str, trades: tuple[str, ...], other: str = OTHERS[0]) -> tuple[str, ...]:
    """Every phrase whose truth is `truth`, about one vendor."""
    if truth not in set(CLASSES.values()):
        raise ValueError(truth)
    return tuple(p for cls, t in CLASSES.items() if t == truth for p in phrases_of(cls, legal, trades, other))


def phrase(truth: str, legal: str, trades: tuple[str, ...]) -> str:
    """The bank's first phrase for a truth (the F2 company moves use it)."""
    return phrases_of(truth if truth in CLASSES else "vendor", legal, trades)[0]


@lru_cache(maxsize=1)
def _all() -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for legal, trades in VENDORS:
        for other in OTHERS:
            for cls, truth in CLASSES.items():
                for said in phrases_of(cls, legal, trades, other):
                    out.setdefault(said, set()).add(truth)
    return {k: frozenset(v) for k, v in out.items()}


def truths(said: str) -> frozenset[str]:
    """Every truth the bank gives this phrase, over all its vendors (empty when it isn't in the bank)."""
    return _all().get(said, frozenset())
