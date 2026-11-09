"""Lookalike domains (design §4): flagged so they count as change context, never merged."""
from __future__ import annotations

from collections.abc import Iterable
from email.utils import parseaddr

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
    """True if `domain` isn't the vendor's (or a subdomain of it) but a person could misread it as one.

    Each label of the domain is compared with the vendor's name: equal once lookalike characters
    are folded, within two edits, or containing the name (a combosquat such as acme-billing). Any
    punycode label counts. A false positive only puts more into the change context (R7).
    """
    domain = domain.strip().lower().rstrip(".")
    known = [d.strip().lower().rstrip(".") for d in vendor_domains]
    if any(domain == k or domain.endswith("." + k) for k in known):
        return False
    labels = domain.split(".")
    if any(label.startswith("xn--") for label in labels):
        return True
    for k in known:
        name = k.split(".")[0]
        for label in labels[:-1] or labels:
            if _fold(label) == _fold(name):
                return True
            if min(len(label), len(name)) >= MIN_NAME and _edits(label, name) <= MAX_EDITS:
                return True
            if len(name) >= 4 and _fold(name) in _fold(label):
                return True
    return False


def sender_domain(sender: str | None) -> str | None:
    """The domain of a sender, with or without a display name ("Dana <ar@acme.example>")."""
    address = parseaddr(sender or "")[1]
    return address.rsplit("@", 1)[1].strip().lower() if "@" in address else None
