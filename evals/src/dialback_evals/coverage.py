"""Spec 0033's coverage floor for the world generator (spec 0048, C3).

Over a fixed, seeded sample of worlds, every rejection reason must fire in at least 5% of worlds and
every knob path appear in at least 3%; at least half the worlds must hold an eligible number (any one,
the real line every adversarial world plants included: the "never eligible" checks are trivially true only
when a rule refuses everything); at least 20% must hold two or more numbers the
oracle's reference finds nothing against (`two_provable`), and at least 5% must hold two such numbers with
a change-context label on the one the call would go to (`context_label_on_provable`, B6), which I12's
removal half needs. The two-provable floors read the oracle's reference frame, not the rule, so a fixed
rule cannot move them.

Reported next to the floors, with no bar (spec 0048 C3; B7 M9, decided by Sami on Nov 10): the share of
worlds holding an eligible number other than the real line (`eligible_other_than_real`). Counted that way
it read 27.2% on the fixed sample; reaching 50% needed generator changes that distorted the worlds.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable

from .invariants import _fax, _frame, _near_flagged
from .worldgen import profile, run

REASONS = frozenset({"first_seen_in_change_context", "too_few_source_types", "younger_than_minimum_age",
                     "near_miss_of_older_number", "hidden_text_only", "not_vendor_party", "fax_line"})
KNOBS = frozenset({"no_request", "late", "shared_id", "reply_subject", "subdomain", "lookalike", "near_miss_pair",
                   "mixed_visibility", "mixed_party", "mixed_timing", "non_default_settings", "twin_first",
                   "request_label", "follow_up", "early_cutoff",
                   # spec 0048, C2: the boundaries B5 found unreached
                   "no_thread", "bare_request", "late_sighting", "upper_fax", "same_time_near_miss",
                   "age_at_min_age", "age_at_established", "age_at_subject_window", "context_role_claim"})
FLOORS = {"two_provable": 0.20, "context_label_on_provable": 0.05}   # set Nov 8, before measuring (spec 0033)
REASON_FLOOR, KNOB_FLOOR, ELIGIBLE_FLOOR = 0.05, 0.03, 0.5


def reference_provable(world) -> tuple[set[str], set[str]]:
    """The numbers the oracle's reference frame finds nothing against (I1-I5, I7), read from raw inputs
    rather than from the rule, and those of them that also carry a visible label in the change context."""
    f, s = _frame(world), world.settings
    ok, labeled = set(), set()
    for e164, n in f.numbers.items():
        if n.first_seen_in_context or not n.visible or len(n.channels) < s.min_source_types:
            continue
        if n.first_clean is None or f.cutoff - n.first_clean < dt.timedelta(days=s.min_age_days):
            continue
        if any((r.party != "vendor" or _fax(r)) and r.document_id not in f.in_context_ids for r in n.visible):
            continue
        if _near_flagged(f, e164, s):
            continue
        ok.add(e164)
        if any((r.party != "vendor" or _fax(r)) and r.document_id in f.in_context_ids for r in n.visible):
            labeled.add(e164)
    return ok, labeled


def reference_pick(world, provable: set[str]) -> str | None:
    """The number the call would go to among `provable`, ranked as the rule ranks (first seen, then more
    source types, then an AR or billing role, then the number), from the reference frame's clean sightings."""
    f = _frame(world)

    def key(e164: str):
        n = f.numbers[e164]
        return n.first_clean, -len(n.channels), not {r.role for r in n.clean} & {"ar", "billing"}, e164
    return min(provable, key=key, default=None)


def measure(sample: Iterable, real_line: str) -> tuple[Counter, int]:
    """How often each reason, knob path and floor quantity shows up in `sample`, and the sample's size.
    `real_line` is the established line every world of that generator plants, eligible by construction:
    the floor counts it, and `eligible_other_than_real` (reported, no bar) leaves it out."""
    seen, n = Counter(), 0
    for world in sample:
        n += 1
        out = run(world)
        seen.update({r for c in out["candidates"] for r in c["reasons"]})
        seen.update(k for k, v in profile(world).items() if v)
        seen["some_eligible"] += any(c["eligible"] for c in out["candidates"])
        seen["eligible_other_than_real"] += any(c["eligible"] and c["e164"] != real_line for c in out["candidates"])
        ok, labeled = reference_provable(world)
        seen["two_provable"] += len(ok) >= 2
        seen["context_label_on_provable"] += len(ok) >= 2 and reference_pick(world, ok) in labeled
    return seen, n


REPORTED = ("eligible_other_than_real",)   # reported next to the floors, never a bar


def summary(seen: Counter, n: int) -> dict[str, float]:
    """The floors' rates and the reported numbers, for the coverage output."""
    return {k: round(seen[k] / n, 3) for k in [*FLOORS, "some_eligible", *REPORTED]}


def shortfalls(seen: Counter, n: int) -> dict[str, float]:
    """Every floor the sample misses, with the rate it reached."""
    rate = {k: round(seen[k] / n, 3) for k in [*FLOORS, *REASONS, *KNOBS, "some_eligible"]}
    short = {k: rate[k] for k, bar in FLOORS.items() if rate[k] < bar}
    short |= {k: rate[k] for k in REASONS if rate[k] < REASON_FLOOR}
    short |= {k: rate[k] for k in KNOBS if rate[k] < KNOB_FLOOR}
    if rate["some_eligible"] < ELIGIBLE_FLOOR:
        short["some_eligible"] = rate["some_eligible"]
    return short
