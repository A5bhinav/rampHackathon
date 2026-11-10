"""Spec 0046: a guided red-team search on select(), and the archive it keeps.

The score. The must-not-help moves are the probes `metamorphic()` already runs that add attacker
evidence: an in-context contract sighting, the lookalike emails at 30, 100 and 400 days, and the
shared-id twins. For each such probe and each number the base run refuses,

    drop = len(reasons in the base run) - len(reasons after the probe)

and `gain` is the largest drop over the moves in use, or 0 if none is positive. Every reason weighs 1
(Q87), so a move that swaps one reason for another scores 0. A drop of 1 or more that leaves the number
ineligible is a near miss; a number turning eligible is a break, which the `M_*_helped` labels already
report. `gain` reads only the select() runs the oracle makes anyway, so it costs no extra call.
`gain_new` is the same score over the moves no frozen case still fails.

The search. `search` draws worlds with Hypothesis at a pinned seed and a budget of `max_examples`
(inside `pinned_draws`, so a seed draws the same worlds in any process),
evaluates each distinct world once, and never raises inside Hypothesis, so nothing is shrunk or
replayed and a failure never stops it. The `guided` and `guided-new` kinds report their score with
`target()` and leave the hill climbing to Hypothesis. The archive keeps the first world for every label
and, per move, the near miss with the highest drop. `archive_fuzz` then shrinks each new label's world
to a 1-minimal one, freezes it, and writes the near misses to their own file, which never gates.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from dialback_ledger.select import Settings
from hypothesis import HealthCheck, given, settings, target
from hypothesis import seed as pin_seed
from hypothesis.internal.conjecture import providers as _providers
from hypothesis.internal.constants_ast import Constants as _Constants

from .gencorpus import _dump, crash_label, freeze, frozen_labels, rerun, world_digest
from .invariants import MUST_NOT_HELP, Probes, all_violations
from .worldgen import World, fictional, worlds

ALL_MOVES = tuple(MUST_NOT_HELP)           # ("incontext", "lookalike", "shared_id")
KINDS = ("archive", "guided", "guided-new")
V1 = Settings()                            # the v1 defaults the shrinker resets to


# ---- the score -------------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Drop:
    """What one must-not-help probe did to one number the base run refused."""
    move: str                     # incontext | lookalike | shared_id
    probe: str                    # which run of that move (m_ctx, m_look100, twin1:d0-0, ...)
    e164: str
    drop: int                     # reasons before minus reasons after
    before: tuple[str, ...]
    after: tuple[str, ...]
    eligible_after: bool


def drops(probes: Probes) -> tuple[list[Drop], list[str]]:
    """Every (probe, refused number) drop, and the anomalies seen: a refused number missing from a
    probe's output counts as a drop of 0 and is logged."""
    base = probes.base or {}
    refused = {c["e164"]: tuple(c.get("reasons", [])) for c in base.get("candidates", []) if not c.get("eligible")}
    found, anomalies = [], []
    for move, probe, out in probes.runs:
        after = {c["e164"]: c for c in out.get("candidates", [])}
        for e164, before in refused.items():
            c = after.get(e164)
            if c is None:
                anomalies.append(f"{probe}: {e164} is missing from the probe's output")
                found.append(Drop(move, probe, e164, 0, before, (), False))
                continue
            reasons = tuple(c.get("reasons", []))
            found.append(Drop(move, probe, e164, len(before) - len(reasons), before, reasons, bool(c.get("eligible"))))
    return found, anomalies


def gain(found: Iterable[Drop], moves: Iterable[str] = ALL_MOVES) -> int:
    """The largest drop over the moves in use, or 0 if none is positive."""
    use = set(moves)
    return max([0, *(d.drop for d in found if d.move in use)])


def near_misses(found: Iterable[Drop]) -> list[Drop]:
    return [d for d in found if d.drop >= 1 and not d.eligible_after]


def open_moves(failing: Iterable[tuple[str, list[str]]]) -> tuple[str, ...]:
    """The moves `gain_new` uses: those whose label no frozen case still fails."""
    still = {label for _, labels in failing for label in labels}
    return tuple(move for move in ALL_MOVES if MUST_NOT_HELP[move] not in still)


def gain_new_moves(frozen_dir: Path, rule=None) -> tuple[str, ...]:
    """`open_moves` over the frozen cases under `frozen_dir`, re-run against `rule`."""
    return open_moves(rerun(frozen_dir, rule=rule))


@dataclasses.dataclass
class Evaluation:
    labels: list[str]
    gain: int = 0
    gain_new: int = 0
    near_misses: list[Drop] = dataclasses.field(default_factory=list)
    anomalies: list[str] = dataclasses.field(default_factory=list)


def evaluate(world: World, *, rule=None, new_moves: Iterable[str] = ALL_MOVES) -> Evaluation:
    """The oracle's labels on a world and the score read from the same runs. A crash inside select() or
    a check is the label `crash_<Name>` and scores 0."""
    probes = Probes()
    try:
        labels = all_violations(world, rule=rule, probes=probes)
    except Exception as error:  # noqa: BLE001  a crash is a finding of its own (decision 3)
        return Evaluation([crash_label(error)])
    found, anomalies = drops(probes)
    return Evaluation(labels, gain(found), gain(found, new_moves), near_misses(found), anomalies)


# ---- the search ------------------------------------------------------------------------------------

@contextlib.contextmanager
def pinned_draws() -> Iterator[None]:
    """Draw from the seed alone. Hypothesis 6.170 also draws, about one integer in twenty, a literal it
    mined from every first-party module loaded at that moment, so one seed gave different worlds under
    pytest, in the CLI and in a bench worker (and a bench result would have depended on --jobs). Inside
    this block that mined pool is empty; Hypothesis's own built-in constants still apply."""
    get, cache = getattr(_providers, "_get_local_constants", None), getattr(_providers, "CONSTANTS_CACHE", None)
    if get is None or cache is None:
        raise RuntimeError("this Hypothesis has no _get_local_constants: re-check pinned_draws against it")
    _providers._get_local_constants = _Constants
    cache.cache.clear()
    try:
        yield
    finally:
        _providers._get_local_constants = get
        cache.cache.clear()


@dataclasses.dataclass(frozen=True)
class Hit:
    index: int                    # the world's place among the distinct worlds evaluated, from 0
    world: World


@dataclasses.dataclass(frozen=True)
class NearMiss:
    index: int
    world: World
    drop: Drop


@dataclasses.dataclass
class Archive:
    kind: str
    seed: int
    budget: int
    calls: int = 0                                       # test cases Hypothesis ran, duplicates included
    worlds: list[World] = dataclasses.field(default_factory=list)       # distinct worlds evaluated, in order
    digests: list[str] = dataclasses.field(default_factory=list)
    labels: list[list[str]] = dataclasses.field(default_factory=list)
    gains: list[int] = dataclasses.field(default_factory=list)
    gains_new: list[int] = dataclasses.field(default_factory=list)
    first: dict[str, Hit] = dataclasses.field(default_factory=dict)     # label -> the first world showing it
    near: dict[str, NearMiss] = dataclasses.field(default_factory=dict)  # move -> its highest near miss
    anomalies: list[str] = dataclasses.field(default_factory=list)

    def record(self, world: World, digest: str, ev: Evaluation) -> None:
        index = len(self.worlds)
        self.worlds.append(world)
        self.digests.append(digest)
        self.labels.append(ev.labels)
        self.gains.append(ev.gain)
        self.gains_new.append(ev.gain_new)
        for label in ev.labels:
            self.first.setdefault(label, Hit(index, world))
        for d in ev.near_misses:
            best = self.near.get(d.move)
            if best is None or d.drop > best.drop.drop:     # ties keep the first found
                self.near[d.move] = NearMiss(index, world, d)
        self.anomalies += [f"world {index}: {a}" for a in ev.anomalies]


def search(kind: str, *, budget: int, seed: int, rule=None, new_moves: Iterable[str] = ALL_MOVES,
           strategy=None) -> Archive:
    """Run the whole budget at a pinned seed and keep the archive. `budget` is Hypothesis's
    `max_examples`; a world drawn again is not evaluated again, and no world past the budget is."""
    if kind not in KINDS:
        raise ValueError(f"unknown search {kind!r}: one of {', '.join(KINDS)}")
    strategy = worlds() if strategy is None else strategy
    new_moves = tuple(new_moves)
    arc = Archive(kind, seed, budget)
    scores: dict[str, tuple[int, int]] = {}

    @pin_seed(seed)
    @settings(max_examples=budget, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])
    @given(strategy)
    def one(world: World) -> None:
        arc.calls += 1
        digest = world_digest(world)
        if digest not in scores:
            if len(arc.worlds) >= budget:
                return
            ev = evaluate(world, rule=rule, new_moves=new_moves)
            scores[digest] = (ev.gain, ev.gain_new)
            arc.record(world, digest, ev)
        if kind == "guided":
            target(scores[digest][0], label="gain")
        elif kind == "guided-new":
            target(scores[digest][1], label="gain_new")

    with pinned_draws():
        one()
    return arc


# ---- the shrinker ----------------------------------------------------------------------------------

def _with(world: World, *, documents=None, sightings=None, poison=None, chosen: Settings | None = None) -> World:
    kw = dict(world.kwargs)
    if documents is not None:
        kw["documents"] = documents
    if sightings is not None:
        kw["sightings"] = sightings
    if chosen is not None:
        kw["settings"] = chosen
    return dataclasses.replace(world, kwargs=kw, poison=world.poison if poison is None else poison,
                               settings=world.settings if chosen is None else chosen)


def _numbers_out(world: World) -> list[World]:
    rows = world.kwargs["sightings"]
    return [_with(world, sightings=[s for s in rows if s.e164 != e164], poison=world.poison - {e164})
            for e164 in sorted({s.e164 for s in rows})]


def _documents_out(world: World) -> list[World]:
    """Each document removed with its sightings. A copy that shares its id with another keeps the
    sightings, which still point at the other copy. The change request's own document is never removed."""
    kw = world.kwargs
    req = kw["request"].document_id if kw["request"] is not None else None
    out = []
    for i, doc in enumerate(kw["documents"]):
        if doc.id == req:
            continue
        rest = kw["documents"][:i] + kw["documents"][i + 1:]
        rows = kw["sightings"] if any(d.id == doc.id for d in rest) else [
            s for s in kw["sightings"] if s.document_id != doc.id]
        out.append(_with(world, documents=rest, sightings=rows))
    return out


def _sightings_out(world: World) -> list[World]:
    rows = world.kwargs["sightings"]
    return [_with(world, sightings=rows[:i] + rows[i + 1:]) for i in range(len(rows))]


def shrink(world: World, label: str, *, rule=None) -> World:
    """Greedily remove one number, then one document with its sightings, then one sighting, then reset
    the settings to the v1 defaults, keeping each change only if `label` survives, until nothing more
    goes: the result is 1-minimal. The change request and its document are always kept."""
    def holds(w: World) -> bool:
        return label in evaluate(w, rule=rule).labels

    if not holds(world):
        return world
    changed = True
    while changed:
        changed = False
        for smaller in (_numbers_out, _documents_out, _sightings_out):
            i = 0
            while i < len(options := smaller(world)):
                if holds(options[i]):
                    world, changed = options[i], True       # the same index is now the next candidate
                else:
                    i += 1
        if world.settings != V1 and holds(reset := _with(world, chosen=V1)):
            world, changed = reset, True
    return world


# ---- lab fuzz --search archive|guided|guided-new ---------------------------------------------------

@dataclasses.dataclass
class FuzzResult:
    archive: Archive
    frozen: dict[str, str]        # each new label -> the case its shrunk world was frozen as
    counted: list[str]            # labels found that a frozen case already records: counted, not frozen
    near_misses: Path


def _slug(label: str) -> str:
    return label.lower().replace("_", "-")


def write_near_misses(arc: Archive, path: Path) -> Path:
    """The archive's near misses, one per move, with their worlds. Lab numbers only (Q88): a person
    decides which go to the rule's owner. Refuses a real-looking number, as `freeze` does."""
    moves = {}
    for move in ALL_MOVES:
        hit = arc.near.get(move)
        if hit is None:
            moves[move] = None
            continue
        numbers = {s.e164 for s in hit.world.kwargs["sightings"]} | set(hit.world.poison) | {hit.drop.e164}
        if bad := sorted(n for n in numbers if not fictional(n)):
            raise ValueError(f"refusing to write real-looking numbers {bad}: the lab writes only NPA-555-0100 to 0199")
        d = hit.drop
        moves[move] = {"drop": d.drop, "e164": d.e164, "probe": d.probe, "reasons_before": list(d.before),
                       "reasons_after": list(d.after), "world_index": hit.index, "world": _dump(hit.world)}
    body = {"search": arc.kind, "seed": arc.seed, "examples": arc.budget, "worlds": len(arc.worlds),
            "near_misses": moves, "anomalies": arc.anomalies}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def archive_fuzz(kind: str, *, examples: int, seed: int, out_dir: Path, near_misses: Path, rule=None,
                 failing: list[tuple[str, list[str]]] | None = None, strategy=None) -> FuzzResult:
    """Run `search`, shrink and freeze the first world of every label no frozen case records yet (two
    labels that shrink to the same world share one case, which records both), and write the near misses. `failing` is the frozen cases' re-run (`rerun(out_dir)`), which `guided-new`
    reads for its moves; it is re-run here when not given."""
    moves = ALL_MOVES
    if kind == "guided-new":
        moves = open_moves(rerun(out_dir, rule=rule) if failing is None else failing)
    arc = search(kind, budget=examples, seed=seed, rule=rule, new_moves=moves, strategy=strategy)
    known = frozen_labels(out_dir)
    frozen, counted, by_world = {}, [], {}
    for label, hit in arc.first.items():
        if label in known:
            counted.append(label)
            continue
        small = shrink(hit.world, label, rule=rule)
        digest = world_digest(small)
        if digest in by_world:                 # already frozen in this run, under an earlier label
            frozen[label] = by_world[digest]
            continue
        case = f"fuzz-seed{seed}-{_slug(label)}-{digest[:8]}"
        freeze(small, case, out_dir, labels=evaluate(small, rule=rule).labels,
               why=(f"found by lab fuzz --search {kind} at seed {seed} within {examples} examples (world {hit.index}), "
                    f"shrunk by the lab to a 1-minimal world for {label}"))
        frozen[label] = by_world[digest] = case
    return FuzzResult(arc, frozen, counted, write_near_misses(arc, near_misses))
