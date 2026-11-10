"""Spec 0048, part B: a call-report fuzzer (`lab reports`): how answers become facts and outcomes.

No call is simulated. Reports are generated (an honest, full confirmation with up to three things
changed), run through A1's chain (the voice service's report, the core's arrival step, call_facts) and
decide(), and checked: F1 (verified only on an honest, full confirmation, by the generator's truth), F2
(less certainty never helps) and F3 (the company check against the phrase bank's truth). Each check is
first shown failing on a broken double; then each runs on production at seed 0 on 2000 reports
(`report_gate`). Violations are frozen under evals/reports-generated/ and re-run."""
from __future__ import annotations

from functools import lru_cache

import pytest
from dialback_core.calls import CallEndedRequest
from dialback_evals import reports
from dialback_evals.draws import sample

SEED, EXAMPLES = 0, 2000


@lru_cache(maxsize=2)
def _sample(domain: str, examples: int = EXAMPLES) -> tuple:
    return tuple(sample(reports.report_cases(domain), examples=examples, seed=SEED))


def _sendable(body: dict) -> list[str]:
    """Why a report body is one production never sends (empty when it could send it): answers on any
    result but answers_recorded (A16), an inbound direction, a dialed number reported as not the pick,
    and anything the core's own CallEndedRequest refuses."""
    why = []
    if "answers" in body and body["result"] != "answers_recorded":
        why.append("answers_without_answers_recorded")
    if body.get("direction") == "inbound":
        why.append("inbound")
    if body.get("dialed_number_is_pick") is False:
        why.append("pick_false")
    try:
        CallEndedRequest.model_validate(body)
    except Exception as error:  # noqa: BLE001  any refusal is a reason
        why.append(f"refused: {type(error).__name__}")
    return why


def test_reachable_domain_matches_production():
    # Acceptance: part B's reachable domain can't produce what production never sends. Every reachable
    # report is one production could send, earlier calls carry no answers (A16), and the contract-only
    # domain does produce bodies production never sends, so the line between them is real.
    reach, contract = _sample("reachable", 400), _sample("contract", 400)
    bad = [(i, _sendable(reports.run(c).body)) for i, c in enumerate(reach) if _sendable(reports.run(c).body)]
    assert bad == []
    assert not [c for c in reach if any("answers" in e for e in c.earlier)]
    only = {w for c in contract for w in _sendable(reports.run(c).body)}
    assert {"answers_without_answers_recorded", "inbound", "pick_false"} <= only
    assert any(any("answers" in e for e in c.earlier) for c in contract)


def test_the_generator_reaches_every_value():
    # Every answer value, comparison result, company truth, call result and answerer comes up.
    seen = reports.coverage(_sample("reachable") + _sample("contract"))
    missing = {k: sorted(v - seen.get(k, set())) for k, v in reports.EXPECTED_COVERAGE.items()
               if v - seen.get(k, set())}
    assert missing == {}


def test_reference_matcher_passes_f3():
    # The phrase bank's truth labels are sound: a strict reference matcher (the vendor's name, or a trade
    # name, and nothing else) never accepts another company, a negation, a reseller or a correction.
    found = reports.check_f3(_sample("reachable") + _sample("contract"), matcher=reports.strict_match)
    assert found.violations == []
    assert found.vendor_answers > 0


def _added(found_broken, found_production) -> list:
    """What a double adds over production on the same reports. Production already fails F1 (FINDINGS A10),
    so a double "failing" F1 proves nothing unless it fails where production does not."""
    return sorted({(i, lab) for i, labs in found_broken.violations for lab in labs}
                  - {(i, lab) for i, labs in found_production.violations for lab in labs})


def test_each_check_catches_its_broken_double():
    reach, contract = _sample("reachable"), _sample("contract")
    # F1: a decide() whose V07 accepts "unsure" (with NR09 off: see the next test).
    assert _added(reports.check_f1(reach, decide=reports.doubles.v07_accepts_unsure), reports.check_f1(reach))
    # F2: one double per move, each letting that move's less certain value through. Where production
    # already fails a move the same way (company_negation: production accepts "Not Acme, this is Globex",
    # FINDINGS A10), the check is shown failing on production itself and the double has nothing to add.
    missed, on_production = [], []
    for move in reports.MOVES:
        cases = contract if move in reports.CONTRACT_MOVES else reach
        production = reports.check_f2(cases, moves=(move,))
        if production.violations:
            on_production.append(move)
        elif not _added(reports.check_f2(cases, decide=reports.doubles.lets_through(move), moves=(move,)), production):
            missed.append(move)
    assert missed == [], missed
    assert set(on_production) <= {"company_negation"}, on_production   # anything else is a new finding
    # F3: a company check that accepts any answer containing the vendor's name fails it; production may
    # fail it the same way (that is A10), and the strict reference matcher passes it (the next test but one).
    assert reports.check_f3(reach, matcher=reports.doubles.contains_vendor_name).violations


def test_v07_accepting_unsure_alone_is_never_seen():
    # Spec 0048 names F1's double "a decide() whose V07 accepts unsure". With the rest of the table intact,
    # NR09 (an unsure answer) fires first, so that double verifies nothing production doesn't, and F1 cannot
    # fail on it (reported as a spec conflict). The double above also turns NR09 off.
    reach = _sample("reachable")
    assert _added(reports.check_f1(reach, decide=reports.doubles.v07_only_accepts_unsure), reports.check_f1(reach)) == []


@pytest.mark.report_gate
def test_checks_on_production():
    # Each check on production at seed 0 on 2000 reports. Gated on the reachable domain only; a violation
    # only in the contract-only domain is a future-state finding for the owner, reported, not gated.
    result = reports.run_checks(examples=EXAMPLES, seed=SEED)
    assert result.gated == [], result.summary()


def test_findings_are_frozen_and_rerun(tmp_path):
    # A violating report is shrunk, frozen and re-run on every run; a frozen report keeps its labels.
    out = tmp_path / "reports-generated"
    first = reports.run_checks(examples=200, seed=SEED, out_dir=out, decide=reports.doubles.v07_accepts_unsure)
    frozen = reports.load_all(out)
    assert first.new_frozen and {label for case in frozen for label in case.labels} >= {"F1_verified_without_full_confirmation"}
    shrunk = next(case for case in frozen if "F1_verified_without_full_confirmation" in case.labels)
    assert len(shrunk.case.answers) <= 7                                # at most one answer per question
    again = reports.rerun(out, decide=reports.doubles.v07_accepts_unsure)
    assert [(c.name, labels) for c, labels in again if labels]              # still failing under the double
    second = reports.run_checks(examples=200, seed=SEED, out_dir=out, decide=reports.doubles.v07_accepts_unsure)
    assert second.new_frozen == []                                      # re-finding a frozen label adds nothing
