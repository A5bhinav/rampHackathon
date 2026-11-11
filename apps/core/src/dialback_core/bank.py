"""Bank-detail checks shared by the clerk portal (spec 0077) and the email door (spec 0086)."""
from __future__ import annotations

import re

ROUTING = re.compile(r"[0-9]{9}")  # used with fullmatch: "$" would accept a trailing newline


def aba_valid(routing: str) -> bool:
    """The ABA routing-number checksum: 3, 7, 1 weights over the nine digits, a multiple of 10."""
    if not ROUTING.fullmatch(routing):
        return False
    d = [int(c) for c in routing]
    return sum(w * x for w, x in zip([3, 7, 1] * 3, d)) % 10 == 0
