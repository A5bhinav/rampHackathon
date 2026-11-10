"""Spec 0083, Task 2: the invariant oracle holds on the real rule, and every label fires when a
deliberately broken rule lets its case through (so each check can actually fail)."""
from __future__ import annotations

import dataclasses
import itertools

import pytest
from dialback_evals.frontier import PLANTED, overblock_world
from dialback_evals.invariants import all_violations, l1_premise, liveness, metamorphic, violations
from dialback_evals.worldgen import (
    VENDOR_DOMAIN,
    NumberSpec,
    SightingSpec,
    clean_worlds,
    make_world,
    run,
    worlds,
)
from dialback_ledger.lookalike import is_lookalike, sender_domain
from dialback_ledger.select import Settings, change_context_documents, select
from hypothesis import given, settings

REAL = "+14155550142"
NUM = "+13035550169"
X = "+13035550101"
# I12, gated Nov 8 and narrowed the same day: a change-context label must never move the call to
# another number. Matched by prefix in the tests below so the label's earlier name is caught too.
CONTEXT_LABEL = "M_incontext_label_moved_pick"
LABEL_PREFIX = "M_incontext_label"


def _rewrite(out: dict, e164: str, **changes) -> dict:
    """A doctored select() output: the named candidate rewritten, everything else as the rule said."""
    return {**out, "candidates": [{**c, **changes} if c["e164"] == e164 else dict(c) for c in out["candidates"]]}


def _eligible_if(out: dict, e164: str, flag: bool) -> dict:
    return {**out, "candidates": [{**c, "eligible": c["eligible"] or (c["e164"] == e164 and flag)}
                                  for c in out["candidates"]]}


@pytest.mark.selection_gate
def test_all_invariants_hold_on_the_real_rule():
    @given(worlds())
    @settings(max_examples=2000, deadline=None, derandomize=True, database=None)
    def check(world):
        assert all_violations(world) == []
    check()


@pytest.mark.parametrize(("label", "spec"), [
    ("I1_first_seen_in_context_eligible", NumberSpec(NUM, ["contract", "listing"], 400, in_thread=True)),
    ("I2_hidden_only_eligible", NumberSpec(NUM, ["contract", "listing"], 400, visibility="white")),
    ("I3_nonvendor_or_fax_eligible", NumberSpec(NUM, ["contract", "listing"], 400, party="bank")),
    ("I3_nonvendor_or_fax_eligible", NumberSpec(NUM, ["contract", "listing"], 400, role="fax")),
    ("I4_too_few_channels_eligible", NumberSpec(NUM, ["contract"], 400)),
    ("I5_too_young_eligible", NumberSpec(NUM, ["contract", "listing"], 10)),
])
def test_each_invariant_catches_a_broken_rule(label, spec):
    world = make_world(numbers=[spec])
    honest = run(world)
    assert label not in violations(world, honest)                                # the real rule passes
    assert label in violations(world, _rewrite(honest, NUM, eligible=True, reasons=[]))   # a leaky rule fails


def test_pick_with_reasons_or_bad_schema_trips_i6():
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500)])
    honest = run(world)
    assert violations(world, honest) == []
    assert "I6_pick_has_reasons" in violations(world, _rewrite(honest, REAL, reasons=["fax_line"]))
    assert "I6_schema" in violations(world, {**honest, "outcome": "maybe"})


def test_poison_near_miss_pick_trips_i7():
    real = NumberSpec(REAL, ["contract", "listing"], 200)
    sib = NumberSpec("+14155550143", ["contract", "listing"], 150, near_miss_of=REAL, is_poison=True)
    world = make_world(numbers=[real, sib])
    honest = run(world)
    assert honest["e164"] == REAL and violations(world, honest) == []
    broken = _rewrite(honest, "+14155550143", eligible=True, reasons=[]) | {"e164": "+14155550143"}
    found = set(violations(world, broken))
    assert "I7_near_miss_eligible" in found
    assert not found & {"I1_first_seen_in_context_eligible", "I2_hidden_only_eligible", "I4_too_few_channels_eligible",
                        "I5_too_young_eligible"}   # only the near-miss rule stood in its way


def test_established_near_miss_is_not_a_violation():
    # Two vendor numbers one digit apart, both over 365 days old. Roles make the poison sibling the
    # pick (AR outranks main), so the check really runs: the established carve-out must hold.
    base = NumberSpec(REAL, ["contract", "listing"], 800, role="main")
    sib = NumberSpec("+14155550143", ["contract", "listing"], 800, role="ar", near_miss_of=REAL, is_poison=True)
    world = make_world(numbers=[base, sib])
    assert run(world)["e164"] == "+14155550143"
    assert "I7_near_miss_eligible" not in all_violations(world)


def test_no_pick_world_is_valid():
    world = make_world(numbers=[NumberSpec("+15035550169", ["email"], 5, in_thread=True, is_poison=True)])
    assert run(world)["outcome"] == "no_provable_number" and all_violations(world) == []


def test_clean_world_picks_a_provable_number():
    held = [0, 0]

    @given(clean_worlds())
    @settings(max_examples=2000, deadline=None, derandomize=True, database=None)   # spec Decision 6 floor
    def check(world):
        held[0] += 1
        held[1] += l1_premise(world)
        assert liveness(world, run(world)) == []
    check()
    assert held[1] == held[0]   # clean_worlds always builds a premise-meeting number, so L1 is never vacuous


@pytest.mark.parametrize("label", ["M_determinism", "M_reorder", "M_incontext_helped", "M_hidden_helped",
                                   "M_lookalike_helped"])
def test_each_metamorphic_check_catches_a_broken_rule(label, monkeypatch):
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500), NumberSpec(X, ["contract"], 400)])
    honest = run(world)
    assert metamorphic(world) == []                                              # the real rule passes
    calls = itertools.count()

    def docs(w):
        return w.kwargs["documents"]

    broken = {
        "M_determinism": lambda w: honest | {"inputs_sha256": str(next(calls))},
        "M_reorder": lambda w: honest | {"inputs_sha256": docs(w)[0].id},
        "M_incontext_helped": lambda w: _eligible_if(honest, X, any(d.id == "m_ctx" for d in docs(w))),
        "M_hidden_helped": lambda w: honest | ({"e164": X} if any(s.visibility != "visible"
                                                                  for s in w.kwargs["sightings"]) else {}),
        "M_lookalike_helped": lambda w: _eligible_if(honest, X, any("acrne" in (d.sender or "") for d in docs(w))),
    }[label]
    monkeypatch.setattr("dialback_evals.invariants.run", broken)
    assert label in metamorphic(world)


def test_new_invariants_catch_broken_rules(monkeypatch):
    real = NumberSpec(REAL, ["contract", "listing"], 500)
    picked = run(make_world(numbers=[real]))
    assert "I9_picked_without_request" not in violations(make_world(numbers=[real], request_present=False),
                                                         run(make_world(numbers=[real], request_present=False)))
    assert "I9_picked_without_request" in violations(make_world(numbers=[real], request_present=False), picked)
    empty = {k: v for k, v in picked.items() if k != "e164"} | {"outcome": "no_provable_number"}
    assert liveness(make_world(numbers=[real]), picked) == []
    assert liveness(make_world(numbers=[real]), empty) == ["L1_clean_world_no_pick"]
    late = make_world(numbers=[real, NumberSpec("+14155550199", [], 500,
                                                sightings=(SightingSpec("listing", 500, recorded_late=True),))])
    assert metamorphic(late) == []
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that reads late records trips I10
                        lambda w: picked | {"inputs_sha256": str(len(w.kwargs["documents"]))})
    assert "M_late_changed" in metamorphic(late)
    monkeypatch.setattr("dialback_evals.invariants.run",           # a rule that rewards shared ids trips I11
                        lambda w: picked | {"candidates": [{"e164": REAL, "reasons": [],
                                                            "eligible": len({d.id for d in w.kwargs["documents"]})
                                                            < len(w.kwargs["documents"])}]})
    assert "M_shared_id_helped" in metamorphic(make_world(numbers=[real]))


# The three confirmed ways attacker evidence makes a number eligible (ledger, Task 2 finding). Each
# world is one the first-cut oracle missed; these pin that the widened oracle now reaches it.

def test_oracle_finds_an_unanchored_near_miss_wherever_the_anchor_doc_sits():
    # A second document under the anchor's id un-anchors the near miss. The anchor's document is not
    # the world's first one, so a first-document-only probe never tries it.
    world = make_world(numbers=[NumberSpec("+12065550100", ["contract", "listing"], 600),
                                NumberSpec(REAL, ["contract"], 200),
                                NumberSpec("+14155550143", ["listing", "vendor_portal"], 150,
                                           near_miss_of=REAL, is_poison=True)])
    assert "M_shared_id_helped" in metamorphic(world)


def test_oracle_probes_lookalike_emails_at_several_ages():
    # A lookalike email 30 days back pulls a same-subject anchor into context; a probe 100 days back
    # would put the near miss itself in context too, and miss it.
    s = Settings(min_source_types=1, min_age_days=0, near_miss_distance=1, same_subject_window_days=30)
    world = make_world(numbers=[NumberSpec(REAL, ["contract"], 40, subject_in_context=True),
                                NumberSpec("+14155550143", ["listing"], 35, near_miss_of=REAL, is_poison=True)],
                       settings=s)
    assert "M_lookalike_helped" in metamorphic(world)


def test_oracle_tries_a_shared_id_twin_that_sorts_first():
    # A second document under a lookalike email's id, sorting ahead of it, becomes the copy the rule
    # reads: the lookalike stops pulling the cutoff back and the attacker's number becomes provable.
    world = make_world(numbers=[
        NumberSpec("+18675550120", [], 0, sightings=(SightingSpec("email", 300, visibility="white",
                                                                  sender="lookalike"),)),
        NumberSpec("+12065550169", ["contract", "listing"], 200, is_poison=True)])
    assert run(world).get("e164") is None
    assert "M_shared_id_helped" in metamorphic(world)


# Review findings I4 and I5: the structural checks must see current defects and R2's first-seen rule.

def test_reference_counts_a_lookalike_copy_on_a_conflicted_id():
    # On a conflicted id the reference is conservative (a lookalike copy still counts, and the earliest
    # copy dates the id), so the first-sorting-twin erasure shows up structurally, not only via a mutation.
    base = make_world(numbers=[
        NumberSpec("+18675550120", [], 0, sightings=(SightingSpec("email", 300, visibility="white",
                                                                  sender="lookalike"),)),
        NumberSpec("+12065550169", ["contract", "listing"], 200, is_poison=True)])
    look = next(d for d in base.kwargs["documents"] if d.sender and "acrne" in d.sender)
    twin = dataclasses.replace(look, sender="", subject=f"{look.subject} (shared id)")
    world = dataclasses.replace(base, kwargs={**base.kwargs, "documents": [*base.kwargs["documents"], twin]})
    out = run(world)
    assert out["e164"] == "+12065550169"                       # select() reads the twin and picks the number
    assert "I4_too_few_channels_eligible" in violations(world, out)   # the reference still sees the lookalike


def test_near_miss_check_covers_every_eligible_number():
    sib = NumberSpec("+14155550143", ["contract", "listing"], 150, near_miss_of=REAL)   # not poison, not picked
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 200), sib])
    honest = run(world)
    assert honest["e164"] == REAL and "I7_near_miss_eligible" not in violations(world, honest)
    assert "I7_near_miss_eligible" in violations(world, _rewrite(honest, "+14155550143", eligible=True, reasons=[]))


def test_first_seen_in_context_is_checked_even_with_later_clean_evidence():
    num = NumberSpec(NUM, [], 0, sightings=(SightingSpec("email", 500, in_thread=True),
                                            SightingSpec("contract", 300), SightingSpec("listing", 300)))
    world = make_world(numbers=[num])
    honest = run(world)
    assert honest.get("e164") is None and "I1_first_seen_in_context_eligible" not in violations(world, honest)
    assert "I1_first_seen_in_context_eligible" in violations(world, _rewrite(honest, NUM, eligible=True, reasons=[]))


def test_first_seen_probe_catches_a_rule_that_ignores_it(monkeypatch):
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500)])
    honest = run(world)
    assert "M_first_seen_ignored" not in metamorphic(world)
    monkeypatch.setattr("dialback_evals.invariants.run", lambda w: honest)   # blind to the older sighting
    assert "M_first_seen_ignored" in metamorphic(world)


# The reachable finding, gated Nov 8 (I12): a label in the change context (bank, fax, or none at all,
# which ingestion records as party "unknown") must never move the call to another number. Refusing
# the case is allowed. Two fix shapes stand in for the rule's owner's choice; both must pass.

def _context_blind(world):
    """One fix shape: a sighting on a document in select()'s own change context keeps its timing and
    visibility but loses its party and role. A number first seen in the context is still refused, by R2."""
    kw = world.kwargs
    ctx = {d.id for d in change_context_documents(**kw)}
    rows = [dataclasses.replace(s, party="vendor", role="main") if s.document_id in ctx else s for s in kw["sightings"]]
    return select(**{**kw, "sightings": rows})


def _fail_closed(world):
    """The other fix shape, as the scratch trial measured it (review 2, B2): today's per-number checks
    and eligibility flags, unchanged. The rule calls only the numbers, from the top of its call list
    (the pick, then up to two alternates), on which it agrees with the same ranking made with
    change-context labels set aside. If the two disagree on the pick, it calls no one, with a
    schema-valid no_provable_number and a reason for a person."""
    out = select(**world.kwargs)

    def calls(o):
        return ([o["e164"]] if o.get("e164") else []) + [a["e164"] for a in o.get("alternates", [])]

    mine, theirs = calls(out), calls(_context_blind(world))
    agreed = 0
    while agreed < min(len(mine), len(theirs)) and mine[agreed] == theirs[agreed]:
        agreed += 1
    if not mine or agreed == len(mine):
        return out
    if agreed:
        return out | {"alternates": out["alternates"][:agreed - 1]}
    return {k: v for k, v in out.items() if k not in ("e164", "contact_point_id", "source_types", "alternates")} | {
        "outcome": "no_provable_number",
        "reason": "The change request's own text would change which number is called, so none is."}


_PATHS = {   # each way a document lands in the change context
    # The request email itself: where real ingestion puts the attacker's own words (review 2, B1).
    "request": lambda d, req: d.id == req.document_id,
    "thread": lambda d, req: (d.thread_id == req.thread_id and d.id != req.document_id
                              and sender_domain(d.sender) != VENDOR_DOMAIN and d.received_at <= req.received_at),
    # A reply in the thread from the vendor's own domain: a hijacked mailbox.
    "thread_own": lambda d, req: (d.thread_id == req.thread_id and d.id != req.document_id
                                  and sender_domain(d.sender) == VENDOR_DOMAIN and d.received_at <= req.received_at),
    "subject": lambda d, req: d.subject == req.subject and d.thread_id != req.thread_id,
    # A forwarded or replied copy of the subject in another thread (re-review K1).
    "fwd_subject": lambda d, req: (d.subject != req.subject and (req.subject or "") in (d.subject or "")
                                   and d.thread_id != req.thread_id),
    # A homoglyph lookalike, and a hyphenated combosquat (re-review K1), are different paths.
    "lookalike": lambda d, req: bool((sd := sender_domain(d.sender)) and is_lookalike(sd, (VENDOR_DOMAIN,))
                                     and "-" not in sd),
    "combosquat": lambda d, req: bool((sd := sender_domain(d.sender)) and is_lookalike(sd, (VENDOR_DOMAIN,))
                                      and "-" in sd),
    # A follow-up that arrives after the request, in a thread and under a subject of its own: in the
    # change context only because it came after the cutoff (re-review K1).
    "after": lambda d, req: d.received_at > req.received_at and d.thread_id != req.thread_id,
}
_KINDS = {"bank": lambda s: s.party == "bank" and s.role != "ar",
          "bank_ar": lambda s: s.party == "bank" and s.role == "ar",   # "Remit to our bank: ..." (re-review K4)
          "fax": lambda s: s.role == "fax",
          "unlabeled": lambda s: s.party == "unknown", "customer": lambda s: s.party == "customer"}


def _partial_fix(kind: str, path: str):
    """The blind fix, except that one kind of label on one context path still counts: the real rule then
    refuses the number and calls the next one."""
    def rule(w):
        kw, req = w.kwargs, w.kwargs["request"]
        docs = {d.id: d for d in kw["documents"]}
        ctx = {d.id for d in change_context_documents(**kw)}
        rows = [s if s.document_id not in ctx or (_KINDS[kind](s) and _PATHS[path](docs[s.document_id], req))
                else dataclasses.replace(s, party="vendor", role="main") for s in kw["sightings"]]
        return select(**{**kw, "sightings": rows})
    return rule


def _two_numbers():
    """The real number picked (500 days), and a second provable number below it (400 days)."""
    return make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 500), NumberSpec(X, ["contract", "listing"], 400)])


def _label_hits(world) -> list[str]:
    return [m for m in metamorphic(world) if m.startswith(LABEL_PREFIX)]


@pytest.mark.parametrize("path", sorted(_PATHS))
@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_context_label_probe_catches_a_rule_that_moves_the_call(kind, path, monkeypatch):
    world = _two_numbers()
    assert run(world)["e164"] == REAL
    monkeypatch.setattr("dialback_evals.invariants.run", _context_blind)
    assert metamorphic(world) == []                       # a rule that ignores change-context labels passes
    monkeypatch.setattr("dialback_evals.invariants.run", _partial_fix(kind, path))
    assert _label_hits(world) == [CONTEXT_LABEL]          # one leaky kind on one path moves the call to X


def test_label_already_in_the_world_that_moved_the_call_is_caught(monkeypatch):
    # The reachable finding as it arrives: the attacker's in-thread bank line is already there, the real
    # number is refused, and the planted number is the pick. Adding more labels to that pick only
    # refuses it too; the check must also see that the label already there moved the call.
    planted = overblock_world("bank_label_in_thread", 0, planted=True)
    leaky = _partial_fix("bank_ar", "thread")             # the world's label is (ar, bank): role-bearing
    assert leaky(planted)["e164"] == PLANTED and _context_blind(planted)["e164"] == REAL
    monkeypatch.setattr("dialback_evals.invariants.run", leaky)
    assert _label_hits(planted) == [CONTEXT_LABEL]


def _labels_under(rule, world) -> set[str]:
    """Every label the whole oracle finds on `world` with `rule` standing in for select()."""
    import dialback_evals.invariants as inv
    real = inv.run
    inv.run = rule
    try:
        return set(all_violations(world))
    finally:
        inv.run = real


def test_a_rule_that_refuses_the_case_passes_i12():
    # Refusing the case when attacker text would change who is called is a valid fix: the call goes
    # nowhere, and the refusal is a schema-valid no_provable_number (review 2, B2).
    planted = overblock_world("bank_label_in_thread", 0, planted=True)
    out = _fail_closed(planted)
    assert out.get("e164") is None and "I6_schema" not in violations(planted, out)
    assert _labels_under(_fail_closed, _two_numbers()) == set()
    assert _labels_under(_fail_closed, planted) == set()


def test_fail_closed_fix_adds_no_violation_today_lacks():
    # The whole oracle, not only I3 and I12: the fail-closed shape must never trip I12, and must add no
    # label today's rule does not already have in the same world (review 2, B2).
    def today(world):
        return select(**world.kwargs)

    @given(worlds())
    @settings(max_examples=200, deadline=None, derandomize=True, database=None)
    def check(world):
        fixed = _labels_under(_fail_closed, world)
        assert not {m for m in fixed if m.startswith(LABEL_PREFIX)}, fixed
        assert fixed <= _labels_under(today, world), fixed - _labels_under(today, world)
    check()


def test_context_blind_fix_opens_the_lookalike_hole():
    # Why ignoring change-context labels is not a valid fix, though it passes I12: an attacker's
    # lookalike email pulls the cutoff earlier, a clean listing that calls X a bank's line lands in
    # the change context, the label is ignored, and X (older than the real line) becomes eligible.
    x = NumberSpec(X, [], 0, is_poison=True, sightings=(SightingSpec("contract", 400), SightingSpec("listing", 400),
                                                         SightingSpec("listing", 20, party="bank")))
    world = make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], 200), x])
    assert "M_lookalike_helped" not in _labels_under(lambda w: select(**w.kwargs), world)
    assert "M_lookalike_helped" in _labels_under(_context_blind, world)
    assert "M_lookalike_helped" not in _labels_under(_fail_closed, world)


def test_context_blind_rule_passes_the_planted_bank_label_world(monkeypatch):
    # The attacker's in-thread email names the real number as a bank line, and a planted provable
    # number ranks below the real one. Ignoring change-context labels keeps the call on the real
    # number, and the oracle must accept that output (I3 reads labels from outside the context only),
    # even though the shape is not a valid fix overall (the test above).
    world = overblock_world("bank_label_in_thread", 0, planted=True)
    assert _context_blind(world)["e164"] == REAL
    monkeypatch.setattr("dialback_evals.invariants.run", _context_blind)
    assert all_violations(world) == []


def test_context_blind_rule_never_trips_i3_or_i12(monkeypatch):
    monkeypatch.setattr("dialback_evals.invariants.run", _context_blind)

    @given(worlds())
    @settings(max_examples=300, deadline=None, derandomize=True, database=None)
    def check(world):
        out = _context_blind(world)
        assert "I3_nonvendor_or_fax_eligible" not in violations(world, out)
        assert not [m for m in metamorphic(world, out) if m.startswith(LABEL_PREFIX)]
    check()



# Re-review K2: a backup number (alternate) is a call too. A label in the change context may trim the
# call list, never change it: what is called must be a prefix of what would be called without it.

def _three_numbers_label_on_the_backup():
    """Three provable numbers (A oldest, then B, then C) and the attacker's bare mention of B in the
    request email: B is refused, so today the backups shift from [B, C] to [C]."""
    a, b, c = "+12015550111", "+13125550122", "+14235550133"
    base = make_world(numbers=[NumberSpec(a, ["contract", "listing"], 600), NumberSpec(b, ["contract", "listing"], 500),
                               NumberSpec(c, ["contract", "listing"], 400),
                               NumberSpec(b, [], 0, sightings=(SightingSpec("email", 0, role="unknown", party="unknown",
                                                                            on_request=True),))])
    return base, a, b, c


def test_fail_closed_trims_backups_and_a_version_that_does_not_is_caught(monkeypatch):
    # never_trims calls the same pick as the fail-closed shape, so I12's pick checks cannot see it; only
    # comparing the whole call list can (the backups shift from [B, C] to [C]).
    world, a, _b, _c = _three_numbers_label_on_the_backup()
    out = _fail_closed(world)
    assert out["e164"] == a and out["alternates"] == []          # only the agreed prefix is called
    monkeypatch.setattr("dialback_evals.invariants.run", _fail_closed)
    assert _label_hits(world) == []

    def never_trims(w):
        o = _fail_closed(w)
        return select(**w.kwargs) if o.get("e164") else o           # keeps today's shifted backups
    monkeypatch.setattr("dialback_evals.invariants.run", never_trims)
    assert _label_hits(world) == [CONTEXT_LABEL]


# Spec 0048, C2: a request without a thread (or without a thread and a subject) opens no thread or subject
# path. Probes that would sit on those paths are not built, so a document the rule reads as outside the
# change context is never treated as inside it. Found when the knob first ran: an earlier lookalike email
# whose first-sorting twin hides it pulls only the oracle's conservative cutoff back, so a thread-less
# "thread reply" at the request's time looked in context to the oracle and not to the rule, and the
# context-blind fix was flagged for I12.

def _threadless(request_subject: bool):
    numbers = [NumberSpec("+12015550100", [], 0, sightings=(
                   SightingSpec("email", 0, role="main"),
                   SightingSpec("email", 1, role="main", sender="lookalike", shared_id=True, twin_first=True))),
               NumberSpec(REAL, ["contract", "listing"], 500),
               NumberSpec(X, ["contract", "listing"], 90, is_poison=True)]
    return make_world(numbers=numbers, request_thread=False, request_subject=request_subject)


@pytest.mark.parametrize("request_subject", [True, False], ids=["no-thread", "bare"])
def test_a_threadless_request_opens_no_thread_path(request_subject, monkeypatch):
    world = _threadless(request_subject)
    assert world.kwargs["request"].thread_id is None
    assert _context_blind(world)["e164"] == REAL
    monkeypatch.setattr("dialback_evals.invariants.run", _context_blind)
    assert metamorphic(world) == []


# Spec 0048, C4 (FINDINGS B7, M7 and M13).

def _near_pair(lead_a: int, lead_b: int):
    """Two vendor numbers one digit apart, each in a contract and a listing, first seen at the given leads."""
    return make_world(numbers=[NumberSpec(REAL, ["contract", "listing"], lead_a),
                               NumberSpec("+14155550143", ["contract", "listing"], lead_b, near_miss_of=REAL)])


def _flagged(world, rule=select) -> set[str]:
    return {c["e164"] for c in rule(**world.kwargs)["candidates"] if "near_miss_of_older_number" in c["reasons"]}


def test_near_miss_edges_at_365_days_and_a_same_time_tie():
    from test_breaks_selection import B5, changed_rule
    changes = {name: changed_rule(name, old, new) for name, old, new in B5}
    from dialback_evals.invariants import _frame, _near_flagged

    def oracle(world) -> set[str]:
        f = _frame(world)
        return {e for e in f.numbers if _near_flagged(f, e, world.settings)}

    at_line, inside, tie = _near_pair(400, 365), _near_pair(400, 364), _near_pair(200, 200)
    # Exactly 365 days is established: neither flags; a day younger, the younger one does.
    assert _flagged(at_line) == oracle(at_line) == set()
    assert _flagged(inside) == oracle(inside) == {"+14155550143"}
    # First seen at the same time and younger than the line: each flags the other.
    assert _flagged(tie) == oracle(tie) == {REAL, "+14155550143"}
    # Each edge tells production from the rule changed at it.
    assert _flagged(at_line, changes["established-line-strict"].select) == {"+14155550143"}
    assert _flagged(tie, changes["same-time-near-misses-do-not-flag"].select) == set()


def test_invariants_imports_no_private_worldgen_name():
    import ast
    from pathlib import Path

    import dialback_evals.invariants as inv
    tree = ast.parse(Path(inv.__file__).read_text(encoding="utf-8"))
    private = [a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "worldgen"
               for a in node.names if a.name.startswith("_")]
    assert private == []


def test_schema_is_found_from_the_package(tmp_path):
    from pathlib import Path

    from dialback_evals.invariants import schema_path
    root = Path(__file__).resolve().parents[2]
    assert schema_path() == root / "packages/contracts/schemas/selection.schema.json"
    deeper = root / "evals" / "src" / "dialback_evals" / "a" / "b"             # how deep the package sits no longer matters
    assert schema_path(deeper) == schema_path()
    with pytest.raises(FileNotFoundError, match="selection.schema.json"):
        schema_path(tmp_path)
