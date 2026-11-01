"""Compare the digits a person said with the case's accounts, in memory (spec 0002, D7).

Tested in the design's order: inconsistent (two different answers), ambiguous (the old and
new accounts end in the same four digits), match, matches_old_account, near_miss (one digit
off, or two neighbors swapped), mismatch. Fewer than four digits, "I don't know" or a
refusal is not_provided. The digits are never returned or stored; only the result is.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

LAST_FOUR_RESULTS = ("match", "matches_old_account", "near_miss", "mismatch", "not_provided", "ambiguous", "inconsistent")
NO_DIGITS = frozenset({"dont_know", "refused"})
_DIGITS = re.compile(r"^[0-9]{1,4}$")
_FOUR = re.compile(r"^[0-9]{4}$")


def near_miss(said: str, expected: str) -> bool:
    """One digit different, or two neighboring digits swapped."""
    diffs = [i for i, (a, b) in enumerate(zip(said, expected, strict=True)) if a != b]
    if len(diffs) == 1:
        return True
    return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and said[diffs[0]] == expected[diffs[1]] and said[diffs[1]] == expected[diffs[0]]


def compare_last_four(spoken: Sequence[str], new_last4: str, old_last4: str | None) -> str:
    """`spoken` is every value recorded for the account_last4 slot on one call, in order."""
    if not _FOUR.match(new_last4) or (old_last4 is not None and not _FOUR.match(old_last4)):
        raise ValueError("account last fours must be exactly four digits")
    for value in spoken:
        if value not in NO_DIGITS and not _DIGITS.match(value):
            raise ValueError("a spoken answer must be 1 to 4 digits, dont_know or refused")
    given = [v for v in spoken if v not in NO_DIGITS]
    if len(set(given)) > 1:
        return "inconsistent"
    if not given or len(given[0]) < 4:
        return "not_provided"
    said = given[0]
    if old_last4 is not None and old_last4 == new_last4:
        return "ambiguous"
    if said == new_last4:
        return "match"
    if old_last4 is not None and said == old_last4:
        return "matches_old_account"
    if near_miss(said, new_last4):
        return "near_miss"
    return "mismatch"
