"""Facts production never computes the way a scenario sets them (spec 0048, A3).

A voice scenario whose verdict rests on one of these lists it under `declared`, the loader refuses one
that sets it without declaring it (or declares one it doesn't set), and the scorecard reports declared
scenarios on their own, outside the per-class defense rows. Each entry says why production never
computes it. When production starts computing one (spec 0044 adds line risk), its entry goes and the
scenarios that declared it are refrozen.
"""
from __future__ import annotations

from typing import Any

UNCOMPUTED: dict[str, str] = {
    "line_risk": "Nothing in production computes line risk; the worker never sets it (FINDINGS A7, spec 0044).",
    "dialed_number_is_pick": "The voice service sets it true on every verified call (with_connect_facts), so false "
                             "never reaches the core.",
    "direction": "No production path places or reports an inbound verification call.",
    "offered_number_from_request": "The ledger can only place a number the caller offered, and the scripted line "
                                   "offers none, so it is never true.",
}


def uncomputed(facts: Any, caller: Any) -> set[str]:
    """The facts a scenario sets to a value production never computes (`facts` is DeclaredFacts, `caller`
    CallerBehavior)."""
    found = set()
    if facts.line_risk is not None:
        found.add("line_risk")
    if facts.dialed_number_is_pick is False:
        found.add("dialed_number_is_pick")
    if facts.direction == "inbound":
        found.add("direction")
    if facts.offered_number_from_request is True and not caller.offer_other_number:
        found.add("offered_number_from_request")
    return found
