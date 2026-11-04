"""Lookalike domains (design §4): flagged so they count as change context, never merged."""
from __future__ import annotations

from collections.abc import Iterable

HOMOGLYPHS = (("rn", "m"), ("vv", "w"), ("1", "l"), ("0", "o"), ("5", "s"))
MAX_EDITS = 2
MIN_NAME = 5  # shorter names are too close to everything to judge by edits


def _name(domain: str) -> str:
    """The registrable part without the public suffix: brightwell.example -> brightwell."""
    labels = domain.strip().lower().rstrip(".").split(".")
    return ".".join(labels[:-1]) if len(labels) > 1 else labels[0]


def _fold(name: str) -> str:
    for a, b in HOMOGLYPHS:
        name = name.replace(a, b)
    return name.replace("-", "")


def _edits(a: str, b: str) -> int:
    """Optimal string alignment distance (Damerau-Levenshtein with adjacent swaps)."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def is_lookalike(domain: str, vendor_domains: Iterable[str]) -> bool:
    """True if `domain` isn't one of the vendor's but a person could misread it as one."""
    domain = domain.strip().lower().rstrip(".")
    known = [d.strip().lower().rstrip(".") for d in vendor_domains]
    if domain in known:
        return False
    name = _name(domain)
    for k in known:
        kn = _name(k)
        if _fold(name) == _fold(kn):
            return True
        if min(len(name), len(kn)) >= MIN_NAME and _edits(name, kn) <= MAX_EDITS:
            return True
    return False


def sender_domain(sender: str | None) -> str | None:
    return sender.rsplit("@", 1)[1].strip().lower() if sender and "@" in sender else None
