"""Spec 0046: a guided red-team search on select(), its score, and the archive it keeps.

The score `gain` reads only the select() runs the oracle already makes: for each probe that adds
attacker evidence (the must-not-help moves) and each number the base run refuses, how many reasons
the move took away. These tests show that exposing those probe outputs changes no label, that the
score is 0 when it should be and 1 for a single removed defense, that the guided searches make no
extra select() calls, and that the archive search runs its whole budget, freezes 1-minimal worlds,
treats a crash as a finding and never lets a near miss gate."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path

from dialback_evals import guided
from dialback_evals.cli import main
from dialback_evals.gencorpus import _load, freeze, load_all, load_world, rerun, world_digest
from dialback_evals.guided import (
    ALL_MOVES,
    KINDS,
    Drop,
    Evaluation,
    archive_fuzz,
    evaluate,
    open_moves,
    search,
)
from dialback_evals.invariants import Probes, all_violations
from dialback_evals.redteam import load_rule
from dialback_evals.worldgen import NumberSpec, make_world, worlds
from hypothesis import HealthCheck, given, settings
from hypothesis import seed as pin_seed
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SRC = ROOT / "evals" / "src" / "dialback_evals"
REAL = "+14155550142"
NUM = "+13035550169"
real_rule = importlib.import_module("dialback_ledger.select")   # the module: the package re-exports select()
SEED0_BUDGET = 60   # enough worlds at seed 0 for today's rule to show two labels (I12 and I11)


def _pinned():
    """select.py as breaks.json pins it (commit e3d57b5), loaded as a rule module from the test copy."""
    spec = json.loads((ROOT / "evals" / "redteam" / "breaks.json").read_text(encoding="utf-8"))
    data = (FIXTURES / "select-e3d57b5.py.txt").read_bytes()
    assert hashlib.sha256(data).hexdigest() == spec["select_sha256"]
    return load_rule(data.decode("utf-8"), "fixture-e3d57b5")


def _sample(n: int, seed: int) -> list:
    """The worlds worldgen draws at a pinned seed, as a search with that budget sees them."""
    seen = []

    @pin_seed(seed)
    @settings(max_examples=n, deadline=None, database=None, suppress_health_check=[HealthCheck.too_slow])
    @given(worlds())
    def collect(world):
        seen.append(world)
    with guided.pinned_draws():
        collect()
    return seen


class _Rule:
    """The real rule, as a rule module the oracle can be handed, counting its select() calls."""
    change_context_documents = staticmethod(real_rule.change_context_documents)

    def __init__(self) -> None:
        self.calls = 0

    def select(self, **kw) -> dict:
        self.calls += 1
        return real_rule.select(**kw)


# ---- 1: exposing the probe outputs changes no label ----------------------------------------------

def test_exposing_probes_keeps_every_label(monkeypatch):
    record = json.loads((FIXTURES / "labels-before-0046.json").read_text(encoding="utf-8"))
    pinned = _pinned()
    sample = _sample(record["examples"], record["seed"])
    assert [world_digest(w) for w in sample] == [digest for digest, _ in record["worlds"]], (
        "the seed-0 sample is not the recorded one (generator or Hypothesis changed): see fixtures/README.md")
    frozen = [(f["case"], _load(f["world"]), f["labels"]) for f in record["frozen"]]
    assert len(frozen) == 9 and any(labels for _, _, labels in frozen)
    # lab fuzz's own path (no rule handed in), pointed at the pinned rule the record was made on
    monkeypatch.setattr("dialback_evals.invariants.run", lambda w: pinned.select(**w.kwargs))
    monkeypatch.setattr("dialback_evals.invariants.change_context_documents", pinned.change_context_documents)
    done: set[str] = set()
    cases = [(digest, w, labels) for w, (digest, labels) in zip(sample, record["worlds"], strict=True)]
    cases += [(case, w, labels) for case, w, labels in frozen]
    for key, world, labels in cases:
        if key in done:
            continue
        done.add(key)
        assert all_violations(world) == labels, key
        assert evaluate(world, rule=pinned).labels == labels, key     # the guided search's path, probes exposed
    assert sum(bool(labels) for _, _, labels in cases) >= 100          # the record is not trivially empty


# ---- 2: no extra select() calls --------------------------------------------------------------------

def test_guided_makes_no_extra_select_calls():
    for kind in KINDS:
        rule = _Rule()
        arc = search(kind, budget=25, seed=0, rule=rule, new_moves=("incontext",))
        assert arc.calls == 25 and arc.worlds
        alone = []
        for world in arc.worlds:            # each distinct world, evaluated by the oracle on its own
            one = _Rule()
            all_violations(world, rule=one)
            alone.append(one.calls)
        assert min(alone) > 0
        assert rule.calls == sum(alone), kind


# ---- 3: the score ----------------------------------------------------------------------------------

def _out(*numbers: tuple[str, list[str]]) -> dict:
    """A select()-shaped output holding just the candidates: (e164, reasons), eligible when no reason."""
    return {"candidates": [{"e164": e, "eligible": not reasons, "reasons": sorted(reasons)} for e, reasons in numbers]}


def test_gain_is_zero_when_nothing_changes():
    before = ["too_few_source_types", "younger_than_minimum_age"]
    probes = Probes(base=_out((REAL, []), (NUM, before)),
                    runs=[(move, f"p-{move}", _out((REAL, []), (NUM, before))) for move in ALL_MOVES])
    found, anomalies = guided.drops(probes)
    assert found and anomalies == []
    assert guided.gain(found) == 0 and guided.near_misses(found) == []
    # a move that adds a reason scores 0 too: only a positive drop counts
    worse = Probes(base=probes.base, runs=[("lookalike", "p", _out((REAL, []), (NUM, [*before, "fax_line"])))])
    assert guided.gain(guided.drops(worse)[0]) == 0
    # a number missing from a probe's output counts as 0 and is logged
    missing = Probes(base=probes.base, runs=[("incontext", "p", _out((REAL, [])))])
    found, anomalies = guided.drops(missing)
    assert guided.gain(found) == 0 and len(anomalies) == 1 and NUM in anomalies[0]


def test_a_swapped_reason_scores_zero():
    probes = Probes(base=_out((NUM, ["too_few_source_types", "younger_than_minimum_age"])),
                    runs=[("incontext", "p", _out((NUM, ["too_few_source_types", "first_seen_in_change_context"])))])
    found, _ = guided.drops(probes)
    assert [d.drop for d in found] == [0]
    assert guided.gain(found) == 0 and guided.near_misses(found) == []


class _DropsOneReason(_Rule):
    """The real rule, except that on the in-context probe's world it takes one reason off NUM and
    leaves it ineligible: a defense the move weakened without breaking it."""

    def select(self, **kw) -> dict:
        out = super().select(**kw)
        if any(d.id == "m_ctx" for d in kw["documents"]):
            out = {**out, "candidates": [
                {**c, "reasons": [r for r in c["reasons"] if r != "younger_than_minimum_age"]} if c["e164"] == NUM else c
                for c in out["candidates"]]}
        return out


def test_one_removed_defense_is_a_near_miss():
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500), NumberSpec(NUM, ["contract"], 10)])
    honest = evaluate(world)
    assert honest.gain == 0 and honest.near_misses == []            # on the real rule nothing drops
    ev = evaluate(world, rule=_DropsOneReason())
    assert ev.gain == 1 and "M_incontext_helped" not in ev.labels  # still ineligible: not a break
    [near] = ev.near_misses
    assert (near.move, near.e164, near.drop, near.eligible_after) == ("incontext", NUM, 1, False)
    assert near.before == ("too_few_source_types", "younger_than_minimum_age") and near.after == ("too_few_source_types",)
    arc = search("archive", budget=1, seed=0, rule=_DropsOneReason(), strategy=st.just(world))
    assert arc.near["incontext"].drop == near and arc.near["incontext"].index == 0


# ---- 4: gain_new leaves out moves a frozen case still fails ----------------------------------------

class _LookalikeHelps(_Rule):
    """The real rule, except that a lookalike sender (acme.example's homoglyph) makes NUM eligible."""

    def select(self, **kw) -> dict:
        out = super().select(**kw)
        if any("acrne" in (d.sender or "") for d in kw["documents"]):
            out = {**out, "candidates": [{**c, "eligible": True} if c["e164"] == NUM else c for c in out["candidates"]]}
        return out


def test_gain_new_skips_moves_with_open_labels(tmp_path):
    assert open_moves([]) == ALL_MOVES
    assert open_moves([("a", ["M_shared_id_helped", "M_incontext_label_moved_pick"]),
                       ("b", ["M_lookalike_helped"])]) == ("incontext",)
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500), NumberSpec(NUM, ["contract"], 400)])
    freeze(world, "case", tmp_path)
    assert guided.gain_new_moves(tmp_path) == ALL_MOVES                       # no frozen case fails a move
    assert rerun(tmp_path, rule=_LookalikeHelps()) == [("case", ["M_lookalike_helped"])]
    assert guided.gain_new_moves(tmp_path, rule=_LookalikeHelps()) == ("incontext", "shared_id")
    probes = Probes(base=_out((NUM, ["too_few_source_types", "younger_than_minimum_age"])),
                    runs=[("lookalike", "p", _out((NUM, ["too_few_source_types"])))])
    found, _ = guided.drops(probes)
    assert guided.gain(found) == 1                                  # gain counts every move
    assert guided.gain(found, ("incontext", "shared_id")) == 0      # gain_new leaves the open one out
    assert guided.gain(found, ALL_MOVES) == 1                       # and uses it again once nothing fails it


# ---- 5: the archive keeps going -------------------------------------------------------------------

def test_archive_keeps_going_after_a_failure():
    for kind in KINDS:
        arc = search(kind, budget=SEED0_BUDGET, seed=0)
        assert arc.calls == SEED0_BUDGET, kind                       # the whole budget ran
        assert len(arc.worlds) == len(set(arc.digests)) <= SEED0_BUDGET
        seen = {label for labels in arc.labels for label in labels}
        assert set(arc.first) == seen and len(seen) >= 2, (kind, seen)   # every distinct label, at least two
        assert min(hit.index for hit in arc.first.values()) < len(arc.worlds) - 1   # it went on after a failure
        for label, hit in arc.first.items():
            assert label in arc.labels[hit.index] and not any(label in ls for ls in arc.labels[:hit.index])


# ---- 6: what the search freezes is 1-minimal ------------------------------------------------------

def _single_removals(world):
    """Every world one number, one document (with its sightings) or one sighting smaller, keeping the
    change request and its document. Written here, apart from the shrinker, so the check is independent."""
    kw = world.kwargs
    request = kw["request"]
    req_id = request.document_id if request is not None else None

    def with_(documents=None, sightings=None, poison=None):
        new = {**kw}
        if documents is not None:
            new["documents"] = documents
        if sightings is not None:
            new["sightings"] = sightings
        return type(world)(kwargs=new, poison=world.poison if poison is None else poison, settings=world.settings)

    for e164 in sorted({s.e164 for s in kw["sightings"]}):
        yield f"number {e164}", with_(sightings=[s for s in kw["sightings"] if s.e164 != e164],
                                      poison=world.poison - {e164})
    for i, doc in enumerate(kw["documents"]):
        if doc.id == req_id:
            continue
        rest = kw["documents"][:i] + kw["documents"][i + 1:]
        rows = kw["sightings"] if any(d.id == doc.id for d in rest) else [s for s in kw["sightings"]
                                                                           if s.document_id != doc.id]
        yield f"document {i} ({doc.id})", with_(documents=rest, sightings=rows)
    for i in range(len(kw["sightings"])):
        yield f"sighting {i}", with_(sightings=kw["sightings"][:i] + kw["sightings"][i + 1:])


def test_frozen_world_is_one_minimal(tmp_path):
    result = archive_fuzz("archive", examples=SEED0_BUDGET, seed=0, out_dir=tmp_path / "cg",
                          near_misses=tmp_path / "out" / "near-misses.json")
    assert len(result.frozen) >= 2 and len(load_all(tmp_path / "cg")) == len(result.frozen)
    for label, case in result.frozen.items():
        world = load_world(tmp_path / "cg" / case)
        assert label in all_violations(world), case
        found = result.archive.first[label].world
        if found.kwargs["request"] is not None:                   # the request and its document are kept
            request = world.kwargs["request"]
            assert request == found.kwargs["request"]
            assert any(d.id == request.document_id for d in world.kwargs["documents"])
        for what, smaller in _single_removals(world):
            assert label not in all_violations(smaller), (case, what)


# ---- 7: a crash is a finding ----------------------------------------------------------------------

class _CrashesOnFax(_Rule):
    """The real rule, except that it raises on any world holding a customer's fax line. No probe of the
    oracle adds that pair (I12 adds a vendor's fax line), so the crash comes from the world itself."""

    def select(self, **kw) -> dict:
        if any((str(s.role), s.party) == ("fax", "customer") for s in kw["sightings"]):
            raise ZeroDivisionError("a planted crash")
        return super().select(**kw)


def test_a_crash_is_a_finding(tmp_path):
    arc = search("archive", budget=20, seed=0, rule=_CrashesOnFax())
    crash = "crash_ZeroDivisionError"
    assert crash in arc.first and arc.calls == 20
    assert arc.first[crash].index < len(arc.worlds) - 1             # the search went on after the crash
    assert any(crash not in labels for labels in arc.labels[arc.first[crash].index + 1:])
    result = archive_fuzz("archive", examples=20, seed=0, out_dir=tmp_path, near_misses=tmp_path / "nm.json",
                          rule=_CrashesOnFax())
    case = result.frozen[crash]
    world = load_world(tmp_path / case)
    assert [(s.role, s.party) for s in world.kwargs["sightings"]] == [("fax", "customer")]   # the one sighting
    assert (case, [crash]) in rerun(tmp_path, rule=_CrashesOnFax())


# ---- 8: near misses never gate --------------------------------------------------------------------

def test_near_misses_never_gate(tmp_path, monkeypatch):
    near = Drop("incontext", "m_ctx", NUM, 1, ("too_few_source_types", "younger_than_minimum_age"),
                ("too_few_source_types",), False)
    monkeypatch.setattr("dialback_evals.guided.evaluate",
                        lambda world, **k: Evaluation(labels=[], gain=1, gain_new=1, near_misses=[near]))
    cg, nm = tmp_path / "corpus-generated", tmp_path / "out" / "near-misses.json"
    args = ["fuzz", "--search", "guided", "--examples", "5", "--seed", "0", "--out", str(cg), "--near-misses", str(nm)]
    assert main(args) == 0                                           # a near miss alone never fails the run
    data = json.loads(nm.read_text(encoding="utf-8"))
    assert data["near_misses"]["incontext"]["drop"] == 1 and data["near_misses"]["incontext"]["e164"] == NUM
    assert load_all(cg) == [] and not list(cg.rglob("*.json"))     # and nothing reaches corpus-generated
    monkeypatch.setattr("dialback_evals.guided.evaluate",
                        lambda world, **k: Evaluation(labels=["M_reorder"], gain=1, gain_new=1, near_misses=[near]))
    assert main(args) == 1                                           # a label still fails it
    assert len(load_all(cg)) == 1


# ---- 13: the new modules import only the pure rule ------------------------------------------------

ALLOWED = ("dialback_ledger.select", "dialback_ledger.lookalike")


def _foreign_imports(source: str) -> list[str]:
    """Imports outside the pure rule, its lookalike classifier, the lab's own modules, Hypothesis and
    the standard library."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative: the lab's own package
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if not (name in ALLOWED or top in ("hypothesis", "dialback_evals", "__future__")
                    or top in sys.stdlib_module_names):
                bad.append(name)
    return bad


def test_new_modules_import_only_the_pure_rule():
    assert _foreign_imports("import dialback_ledger.store\nfrom jsonschema import x\nfrom .gencorpus import y\n") == [
        "dialback_ledger.store", "jsonschema"]                     # the check can fail
    for name in ("guided.py", "redteam.py"):
        assert _foreign_imports((SRC / name).read_text(encoding="utf-8")) == [], name
