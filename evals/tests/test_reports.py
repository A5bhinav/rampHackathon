"""Spec 0048, part B: a call-report fuzzer (`lab reports`): how answers become facts and outcomes.

No call is simulated. Reports are generated (an honest, full confirmation with up to three things
changed), run through A1's chain (the voice service's report, the core's arrival step, call_facts) and
decide(), and checked: F1 (verified only on an honest, full confirmation, by the generator's truth), F2
(less certainty never helps) and F3 (the company check against the phrase bank's truth). Each check is
first shown failing on a broken double; then each runs on production at seed 0 on 2000 reports
(`report_gate`). Violations are frozen under evals/reports-generated/ and re-run."""
from __future__ import annotations

import dataclasses
from functools import lru_cache
from pathlib import Path

import pytest
from dialback_core.calls import CallEndedRequest
from dialback_evals import reports
from dialback_evals.draws import sample

SEED, EXAMPLES = 0, 2000
ROOT = Path(__file__).resolve().parents[2]


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


def _recordable(case) -> list[str]:
    """Why a report's answers are ones production never records (empty when it could), judged here, apart
    from reports.py (spec 0048 review fix 4)."""
    order = ["recording_consent", "company_named", "department", "speaker_name", "speaker_role",
             "change_requested", "account_last4"]
    why = []
    slots = [a["slot"] for a in case.answers]
    if any(s not in order and s != "flag" for s in slots):
        why.append("a slot no production agent records")                 # an offered number, for one
    if any(slots.count(s) > 1 for s in order):
        why.append("an answer recorded twice")                            # only the current question's answer
    if any(a["value"] != "pressure_to_approve" for a in case.answers if a["slot"] == "flag"):
        why.append("a flag other than code's pressure flag")
    consent = [a for a in case.answers if a["slot"] == "recording_consent"]
    if not consent and case.answers:
        why.append("answers without consent")
    if consent and consent[0]["value"] == "no" and len(case.answers) > 1:
        why.append("answers after consent no")
    present = [s for s in order if s in slots]
    if present != order[:len(present)]:
        why.append("a question skipped")                                  # the call asks every question in turn
    turns = [next(a["turn_id"] for a in case.answers if a["slot"] == s) for s in present]
    if turns != sorted(set(turns)) or len(set(turns)) != len(turns):
        why.append("answers out of order")
    if consent and any(a["turn_id"] < consent[0]["turn_id"] for a in case.answers if a["slot"] == "flag"):
        why.append("a flag before consent")
    return why


def test_reachable_answers_are_what_production_records():
    # Spec 0048 review fix 4: a reachable report holds only what production's call records; everything else
    # (offered numbers, other flags, answers twice, after a refused consent or out of order) is contract-only.
    reach, contract = _sample("reachable"), _sample("contract")
    assert [(i, _recordable(c)) for i, c in enumerate(reach) if _recordable(c)] == []
    seen = {why for c in contract for why in _recordable(c)}
    assert seen >= {"a slot no production agent records", "an answer recorded twice",
                    "a flag other than code's pressure flag", "answers after consent no", "answers out of order"}
    assert any(c.values("flag") == ["pressure_to_approve"] for c in reach)          # code's flag stays reachable
    assert any(c.values("recording_consent") == ["no"] for c in reach)              # a refused consent too


def test_f2_and_shrinking_keep_reachable_reports_reachable():
    reach = _sample("reachable", 400)
    for case in reach[:200]:
        for name, move in reports.MOVES.items():
            moved = move(case)
            if moved is not None and reports.reachable(case) and reports.reachable(moved):
                assert _recordable(moved) == [], (name, _recordable(moved))
    stale = reports.with_case(reports.honest(), pick_current=False)
    small = reports.shrink(stale, "F1:pick=stale", decide=lambda f: reports.dialback_decision.decide(
        dataclasses.replace(f, dialed_eligible_number=True)))
    assert _recordable(small) == [] and reports.reachable(small)


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
    # F1: a decide() whose V07 accepts "unsure" and whose NR09 is turned off.
    assert _added(reports.check_f1(reach, decide=reports.doubles.v07_accepts_unsure_nr09_off), reports.check_f1(reach))
    # F2: one double per move, each letting that move's less certain value through. Where production
    # already fails a move the same way (company_negation: production accepts "Not Acme, this is Globex",
    # FINDINGS A10), the check is shown failing on production itself and the double has nothing to add.
    # A move production can't record from a reachable report (a second answer, answers after a refused
    # consent; review fix 4) is shown on the contract-only domain, where it applies.
    missed, on_production = [], []
    for move in reports.MOVES:
        for cases in ((contract,) if move in reports.CONTRACT_MOVES else (reach, contract)):
            production = reports.check_f2(cases, moves=(move,))
            if production.violations:
                on_production.append(move)
                break
            if _added(reports.check_f2(cases, decide=reports.doubles.lets_through(move), moves=(move,)), production):
                break
        else:
            missed.append(move)
    assert missed == [], missed
    assert set(on_production) <= {"company_negation"}, on_production   # anything else is a new finding
    # F3: a company check that accepts any answer containing the vendor's name fails it; production may
    # fail it the same way (that is A10), and the strict reference matcher passes it (the next test but one).
    assert reports.check_f3(reach, matcher=reports.doubles.contains_vendor_name).violations


@pytest.mark.report_gate
def test_checks_on_production():
    # Each check on production at seed 0 on 2000 reports. Gated on the reachable domain only; a violation
    # only in the contract-only domain is a future-state finding for the owner, reported, not gated.
    result = reports.run_checks(examples=EXAMPLES, seed=SEED)
    assert result.gated == [], result.summary()


def test_findings_are_frozen_and_rerun(tmp_path):
    # A violating report is shrunk, frozen and re-run on every run; a frozen report keeps its labels.
    out = tmp_path / "reports-generated"
    first = reports.run_checks(examples=200, seed=SEED, out_dir=out, decide=reports.doubles.v07_accepts_unsure_nr09_off)
    frozen = reports.load_all(out)
    assert first.new_frozen and {label for case in frozen for label in case.labels} >= {"F1:change=unsure"}
    shrunk = next(case for case in frozen if "F1:change=unsure" in case.labels)
    assert len(shrunk.case.answers) <= 7                                # at most one answer per question
    again = reports.rerun(out, decide=reports.doubles.v07_accepts_unsure_nr09_off)
    assert [(c.name, labels) for c, labels in again if labels]              # still failing under the double
    second = reports.run_checks(examples=200, seed=SEED, out_dir=out, decide=reports.doubles.v07_accepts_unsure_nr09_off)
    assert second.new_frozen == []                                      # re-finding a frozen label adds nothing


# ---- spec 0048 review fix 1: F1's label names the condition that failed ---------------------------------

def test_f1_labels_name_the_failed_condition():
    base = reports.honest()
    assert reports.unconfirmed(base) == [] and reports.run(base).decision.outcome == "verified"
    negated = reports.with_company(base, "Not Acme Fasteners, this is Globex Logistics", "negation")
    assert reports.unconfirmed(negated) == ["company=negation"]
    assert reports.check_f1([negated]).violations == [(0, ["F1:company=negation"])]      # production verifies it (A10)
    stale = reports.with_case(base, pick_current=False)
    assert reports.unconfirmed(stale) == ["pick=stale"]
    assert reports.check_f1([stale]).violations == []                                      # production refuses it (V01)


def test_the_known_failures_list_names_only_a10s_three_f1_conditions():
    from dialback_evals import known
    entries = known.load(ROOT / "evals" / "known-failures.json")["lab-reports"]
    f1 = {label for e in entries for label in e.labels if label.startswith("F1")}
    assert f1 == {"F1:company=negation", "F1:company=correction", "F1:company=reseller"}


def _bugged(bug):
    """call_facts with one planted bug (the fresh review's three), for lab reports to catch."""
    import dataclasses

    import dialback_core.call_facts as core_facts
    real = core_facts.call_facts

    def call_facts(report, case, generation, context):
        if bug == "unknown_speaker_not_in_thread" and context.speaker_in_change_thread is None:
            context = dataclasses.replace(context, speaker_in_change_thread=False)
        if bug == "stale_pick_counted_current":         # the first pick read as this version's, not the last
            first = next(e for e in generation if e["type"] == "number_resolved")
            generation = [first] + [e for e in generation if e["type"] != "number_resolved"]
        if bug == "first_company_answer_only" and report.get("answers"):
            answers, seen = [], False
            for a in report["answers"]["answers"]:
                if a["slot"] == "company_named":
                    if seen:
                        continue
                    seen = True
                answers.append(a)
            report = {**report, "answers": {**report["answers"], "answers": answers}}
        return real(report, case, generation, context)
    return call_facts


@pytest.mark.parametrize(("bug", "code"), [(None, 0), ("unknown_speaker_not_in_thread", 1),
                                           ("stale_pick_counted_current", 1), ("first_company_answer_only", 0)])
def test_planted_call_facts_bugs_turn_lab_reports_red(bug, code, tmp_path, monkeypatch, capsys):
    # Spec 0048 review fix 1: the review's planted call_facts bugs, with the repo's known-failures list. The first
    # two turn lab-reports red. The third (only the first company answer read) cannot show on a reachable report:
    # production records the company answer once (review fix 4), so it shows only on contract-only reports, which
    # spec B2 reports and never gates. That contradicts the review-fix acceptance ("each turns lab-reports red")
    # and is reported as a spec conflict; this pins what happens until Sami decides.
    import shutil

    import dialback_core.call_facts as core_facts
    from dialback_evals.cli import main
    out = tmp_path / "reports-generated"
    shutil.copytree(ROOT / "evals" / "reports-generated", out)
    if bug:
        monkeypatch.setattr(core_facts, "call_facts", _bugged(bug))
    assert main(["reports", "--examples", "2000", "--seed", "0", "--out", str(out),
                 "--known-failures", str(ROOT / "evals" / "known-failures.json")]) == code
    if bug == "first_company_answer_only":
        printed = capsys.readouterr().out
        assert "future-state findings (not gated): F1:company=other" in printed



# ---- spec 0048 review fix 2: F1 covers every verify condition --------------------------------------------

def _answer(slot, value, turn):
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"x{turn}_{slot}",
            "captured_at": "2026-11-01T17:30:00Z", "capture_method": "speech"}


def _retimed(case, slot, turn):
    return reports.with_case(case, answers=tuple({**a, "turn_id": turn} if a["slot"] == slot else a
                                                 for a in case.answers))


@pytest.mark.parametrize(("change", "condition"), [
    (lambda c: reports.with_case(c, answers=(*c.answers, _answer("flag", "pressure_to_approve", 13))),
     "flag=pressure_to_approve"),
    (lambda c: reports.with_case(c, answers=(*c.answers, _answer("offered_number", "(312) 555-0142", 13))),
     "offered_number"),
    (lambda c: reports.with_case(c, answers=tuple(a for a in c.answers if a["slot"] != "speaker_name")), "name=missing"),
    (lambda c: reports.with_case(c, answers=tuple(a for a in c.answers if a["slot"] != "speaker_role")), "role=missing"),
    (lambda c: reports.with_case(c, answers=(*c.answers, _answer("speaker_name", "Sam Ortiz", 15))), "speaker=two_names"),
    (lambda c: reports.with_case(c, answers=(*c.answers, _answer("speaker_role", "Controller", 15))), "role=two_values"),
    (lambda c: _retimed(c, "change_requested", 6), "order=out_of_order"),         # the change before the name
    (lambda c: _retimed(c, "recording_consent", 4), "order=out_of_order"),        # consent after the company
])
def test_f1_covers_every_verify_condition(change, condition):
    assert reports.unconfirmed(change(reports.honest())) == [condition]


def test_a_planted_flag_dropping_bug_turns_lab_reports_red(tmp_path, monkeypatch):
    # The review's fifth finding: a call_facts that drops every flag answer verified 30 more reachable reports
    # and F1 did not see it. Flags are reachable only as code's pressure flag (review fix 4), so the bug shows
    # on those.
    import shutil

    import dialback_core.call_facts as core_facts
    from dialback_evals.cli import main
    real = core_facts.call_facts

    def no_flags(report, case, generation, context):
        if report.get("answers"):
            kept = [a for a in report["answers"]["answers"] if a["slot"] != "flag"]
            report = {**report, "answers": {**report["answers"], "answers": kept}}
        return real(report, case, generation, context)
    out = tmp_path / "reports-generated"
    shutil.copytree(ROOT / "evals" / "reports-generated", out)
    monkeypatch.setattr(core_facts, "call_facts", no_flags)
    assert main(["reports", "--examples", "2000", "--seed", "0", "--out", str(out),
                 "--known-failures", str(ROOT / "evals" / "known-failures.json")]) == 1
