"""Spec 0033, Task 4: the known-limit frontier sweep and the over-block sweep.

Both are measurement, not gates: deterministic grids of hand-shaped worlds run through the real
select(). The frontier asks what an attack costs. A poison number pre-seeded across channels and lead
time, against a K01-shape vendor whose own number is seen only by email (so never provable by channel
count), records when the poison becomes the pick. The over-block sweep asks what a defense costs. One
real, provable vendor number plus exactly one piece of attacker noise records when that noise alone
blocks the genuine number. Each holds its real number fixed (spec Q62, Q64), and its summary says so.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import itertools

from dialback_ledger.select import DocumentRef, Settings

from .worldgen import DEFAULT, NumberSpec, SightingSpec, World, make_world, run

# The frontier.
DEFAULT_GRID = {"channels": [1, 2, 3], "lead_days": [0, 30, 60, 90, 180, 365]}
POISON_CHANNELS = ("contract", "listing", "vendor_portal")
K01_REAL = "+19715550142"
POISON = "+13125550169"   # another area code: unrelated to the vendor line
K01_EMAIL_DAYS = (700, 400, 100)


def _k01_real() -> NumberSpec:
    """The vendor's own line, seen in its emails for years: all email is one channel, so never provable alone."""
    return NumberSpec(K01_REAL, [], 0, sightings=tuple(SightingSpec("email", d) for d in K01_EMAIL_DAYS))


def frontier_world(channels: int, lead_days: int, settings: Settings = DEFAULT) -> World:
    poison = NumberSpec(POISON, list(POISON_CHANNELS[:channels]), lead_days, is_poison=True)
    return make_world(numbers=[_k01_real(), poison], settings=settings)


def sweep(grid: dict = DEFAULT_GRID, settings: Settings = DEFAULT) -> list[dict]:
    cells = []
    for channels, lead in itertools.product(grid["channels"], grid["lead_days"]):
        out = run(frontier_world(channels, lead, settings))
        cand = next((c for c in out["candidates"] if c["e164"] == POISON), None)
        cells.append({"channels": channels, "lead_days": lead, "outcome": out["outcome"],
                      "poison_eligible": bool(cand and cand["eligible"]), "poison_picked": out.get("e164") == POISON,
                      "reasons": list(cand["reasons"]) if cand else []})
    return cells


def min_effort(cells: list[dict]) -> dict:
    """The least attacker effort that reaches a pick: the Pareto-minimal (channels, lead_days) cells."""
    picked = {(c["channels"], c["lead_days"]) for c in cells if c["poison_picked"]}
    front = sorted(p for p in picked if not any(o != p and o[0] <= p[0] and o[1] <= p[1] for o in picked))
    if not front:
        return {"channels": None, "lead_days": None, "front": [],
                "summary": "No cell in the grid gets the poison number picked."}
    parts = [f"at least {c} counting channel{'s' if c != 1 else ''} and at least {d} days of lead" for c, d in front]
    summary = ("The poison number becomes the pick with " + " or ".join(parts) + ", against a vendor whose own "
               "number is seen only by email (K01 shape). Against a vendor with a provable number the poison must "
               "also be seen before it to outrank it; the real number is held fixed (spec Q62).")
    return {"channels": front[0][0], "lead_days": front[0][1], "front": [list(p) for p in front], "summary": summary}


def threshold_holds(cells: list[dict], settings: Settings) -> bool:
    return not any(c["poison_picked"] for c in cells
                   if c["channels"] < settings.min_source_types or c["lead_days"] < settings.min_age_days)


def frontier_table(cells: list[dict]) -> dict:
    return {"real": {"e164": K01_REAL, "channels": ["email"], "seen_days": list(K01_EMAIL_DAYS)},
            "grid": {"channels": sorted({c["channels"] for c in cells}),
                     "lead_days": sorted({c["lead_days"] for c in cells})},
            "cells": cells, "min_effort": min_effort(cells)}


# The over-block sweep.
REAL = "+14155550142"
SIBLING = "+14155550143"
UNRELATED = "+18675550188"
REAL_LEAD = 200
NOISE = ("lookalike_email", "in_thread_copy", "same_subject_copy", "near_miss_sibling", "hidden_copy",
         "bank_label_copy", "fax_label_copy", "shared_id_duplicate", "late_copy")
NOISE_AGES = (0, 30, 90, 180, 365, 730)


def _copy(*sightings: SightingSpec) -> NumberSpec:
    """More sightings of the real number itself, in their own documents."""
    return NumberSpec(REAL, [], 0, sightings=sightings)


def overblock_world(noise: str | None, age_days: int, settings: Settings = DEFAULT) -> World:
    """The real number (contract + listing, first seen REAL_LEAD days back) plus one piece of noise."""
    extra = {
        None: None,
        "lookalike_email": NumberSpec(UNRELATED, [], 0, sightings=(SightingSpec("email", age_days, sender="lookalike"),)),
        "in_thread_copy": _copy(SightingSpec("email", age_days, in_thread=True)),
        "same_subject_copy": _copy(SightingSpec("email", age_days, subject="same")),
        "near_miss_sibling": NumberSpec(SIBLING, ["contract", "listing"], age_days, near_miss_of=REAL, is_poison=True),
        "hidden_copy": _copy(SightingSpec("contract", age_days, visibility="white")),
        "bank_label_copy": _copy(SightingSpec("listing", age_days, party="bank")),
        "fax_label_copy": _copy(SightingSpec("listing", age_days, role="fax")),
        "shared_id_duplicate": None,
        "late_copy": _copy(SightingSpec("listing", age_days, recorded_late=True)),
    }[noise]
    numbers = [NumberSpec(REAL, ["contract", "listing"], REAL_LEAD)] + ([extra] if extra else [])
    world = make_world(numbers=numbers, settings=settings)
    if noise != "shared_id_duplicate":
        return world
    when = world.kwargs["requested_cutoff"] - dt.timedelta(days=age_days)
    twin = DocumentRef("d0-0", "contract", when, "ar@other.example", "t_twin", "Doc twin", when)  # the real contract doc's id
    return dataclasses.replace(world, kwargs={**world.kwargs, "documents": [*world.kwargs["documents"], twin]})


def _cell(noise: str | None, age_days: int | None, settings: Settings) -> dict:
    out = run(overblock_world(noise, age_days or 0, settings))
    real = next(c for c in out["candidates"] if c["e164"] == REAL)
    return {"noise": noise, "age_days": age_days, "outcome": out["outcome"], "picked": out.get("e164"),
            "real_picked": out.get("e164") == REAL, "real_reasons": list(real["reasons"])}


def overblock_sweep(settings: Settings = DEFAULT) -> list[dict]:
    return [_cell(None, None, settings)] + [_cell(n, a, settings) for n, a in itertools.product(NOISE, NOISE_AGES)]


def overblock_holds(cells: list[dict]) -> bool:
    base = next(c for c in cells if c["noise"] is None)
    inert = [c for c in cells if c["noise"] in ("hidden_copy", "late_copy")]
    return base["real_picked"] and all(c["real_picked"] for c in inert)


def overblock_table(cells: list[dict]) -> dict:
    summary = {}
    for noise in NOISE:
        mine = [c for c in cells if c["noise"] == noise]
        blocks = [c["age_days"] for c in mine if not c["real_picked"]]
        moved = sorted({c["picked"] for c in mine if not c["real_picked"] and c["picked"]})
        if not blocks:
            line = f"{noise} never blocks the real number"
        elif len(blocks) == len(mine):
            line = f"{noise} blocks the real number at every age"
        else:
            line = f"{noise} blocks the real number at ages {', '.join(map(str, blocks))} days"
        if moved:
            line += f"; the pick moves to {', '.join(moved)}"
        summary[noise] = {"blocks_at": blocks, "moved_to": moved,
                          "line": f"{line} (real number first seen {REAL_LEAD} days before the request; spec Q64)"}
    base = next(c for c in cells if c["noise"] is None)
    return {"real": {"e164": REAL, "channels": ["contract", "listing"], "lead_days": REAL_LEAD},
            "baseline_picked": base["real_picked"], "ages": list(NOISE_AGES), "cells": cells, "summary": summary}
