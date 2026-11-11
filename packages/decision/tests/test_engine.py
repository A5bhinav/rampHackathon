"""decide() as a whole (spec 0006): total, never verifies on what it didn't check, hashes all
of its inputs, and has a sentence for every rule id it can record."""
from __future__ import annotations

from dataclasses import replace

from conftest import CLEAN, GOOD
from dialback_decision import decide, rules
from dialback_decision.engine import ENGINE_VERSION, RETIRED, TEMPLATES, Decision
from dialback_decision.facts import EarlierCall, facts_from_answers
from hypothesis import given
from hypothesis import strategies as st


def test_a_clean_confirmation_is_verified_with_every_condition_named():
    d = decide(GOOD)
    assert d.outcome == "verified" and d.engine_version == ENGINE_VERSION == "decision-1.1.0"
    assert d.rules_fired == tuple(r.id for r in rules.VERIFIED)
    assert all("safe" not in s.lower() for s in d.explanation)


def test_blocks_run_in_order_and_the_first_with_a_match_decides():
    both = replace(GOOD, change_requested=("no",), flags=("pressure_to_approve",))
    assert decide(both).rules_fired == ("NV01_vendor_denied_change",), "Not verified beats Needs review"
    refused = replace(GOOD, recording_consent=("no",), change_requested=("no",))
    assert decide(refused).rules_fired == ("NR02_no_recording_consent",), "input problems beat Not verified"
    several = replace(GOOD, last_four_result="near_miss", flags=("opt_out",))
    assert decide(several).rules_fired == ("NR05_digits_near_miss", "NR11_opt_out"), "every match in the block is listed"


def test_unknown_facts_fail_closed():
    # company_matches is not here: an unknown company answer no longer blocks (spec 0082, D7b, and
    # test_rules.py::test_the_company_answer_is_not_required_to_verify).
    for unknown in ("speaker_in_change_thread", "same_person_yes_and_digits", "dialed_out", "answered_by", "consent_first",
                    "checks", "last_four_result"):
        assert decide(replace(GOOD, **{unknown: None})).outcome == "needs_review", unknown


def test_a_hash_failure_is_an_engine_error_never_a_decision():
    d = decide(replace(GOOD, extra={"unhashable": object()}))
    assert (d.outcome, d.rules_fired, d.inputs_hash) == ("needs_review", ("NR98_engine_error",), "0" * 64)


def test_an_engine_error_goes_to_a_person(monkeypatch):
    def boom(f, m):
        raise RuntimeError("bug")
    monkeypatch.setattr(rules, "NEEDS_REVIEW", (rules.Rule("NR00_boom", "never shown", boom),))
    d = decide(GOOD)
    assert (d.outcome, d.rules_fired) == ("needs_review", ("NR98_engine_error",))


def test_anything_that_isnt_facts_is_invalid():
    for junk in (None, {"call_result": "answers_recorded"}, replace(GOOD, checks="clean")):
        assert decide(junk).rules_fired == ("NR97_facts_invalid",)
    assert decide(GOOD, source="email").rules_fired == ("NR97_facts_invalid",)
    assert decide(GOOD, strict="yes").rules_fired == ("NR97_facts_invalid",)


def test_inputs_hash_covers_the_facts_the_mode_and_the_source():
    h = decide(GOOD).inputs_hash
    assert h == decide(replace(GOOD)).inputs_hash
    assert h != decide(GOOD, strict=True).inputs_hash and h != decide(GOOD, source="manual").inputs_hash
    assert h != decide(replace(GOOD, speaker_named=False)).inputs_hash
    assert decide(replace(GOOD, flags=("other", "opt_out"))).inputs_hash == \
        decide(replace(GOOD, flags=("opt_out", "other"))).inputs_hash, "flag order doesn't matter"
    assert decide(None).inputs_hash == "0" * 64


def test_the_decision_body_follows_the_contract():
    body = decide(GOOD, strict=True).body()
    assert body == {"outcome": "verified", "rules_fired": list(decide(GOOD, strict=True).rules_fired),
                    "engine_version": ENGINE_VERSION, "inputs_hash": body["inputs_hash"], "last_four_result": "match",
                    "strict_mode": True, "source": "call"}
    no_digits = decide(replace(GOOD, last_four_result=None)).body()
    assert "last_four_result" not in no_digits, "the contract's LastFourResult has no null"
    assert "source" not in decide(GOOD, source="email").body(), "an invalid source is left out, never guessed"


# Every id decision-0.x could record (frozen from the skeleton engine), so its decisions still render.
SKELETON_IDS = ["NR01_no_live_person", "NR02_no_recording_consent", "NR03_wrong_company", "NR04_speaker_from_change_thread", "NR05_digits_near_miss", "NR06_digits_not_provided", "NR07_digits_ambiguous", "NR08_digits_inconsistent", "NR09_change_unsure", "NR10_pressure", "NR11_opt_out", "NR12_asked_what_is_checked", "NR13_another_number", "NR14_answer_not_in_transcript", "NR15_script_violation", "NR16_other_flag", "NR17_split_speakers", "NR98_engine_error", "NR99_nothing_decided", "NV01_vendor_denied_change", "NV02_digits_mismatch", "NV03_digits_match_old_account", "NV04_offered_number_from_request", "U01_speaker_thread_not_checked", "U02_same_person_not_checked", "U03_offered_number_source_not_checked", "V01_eligible_number_dialed", "V02_live_person", "V03_recording_consent", "V04_company_matches", "V05_speaker_named", "V06_change_confirmed", "V07_digits_match", "V08_answers_grounded", "V09_script_clean", "V10_no_flags"]


def test_every_skeleton_id_still_renders_and_no_retired_id_is_reused():
    assert len(SKELETON_IDS) == 36 and set(SKELETON_IDS) <= set(TEMPLATES)
    current = {r.id for r in (*rules.BLOCK0, *rules.NOT_VERIFIED, *rules.NEEDS_REVIEW, *rules.VERIFIED, *rules.STRICT)}
    assert not set(RETIRED) & current, "a retired id never comes back with a new meaning"
    assert not set(RETIRED) & set(rules.FIXED)


def test_every_id_that_can_be_recorded_has_a_sentence():
    ids = {r.id for r in (*rules.BLOCK0, *rules.NOT_VERIFIED, *rules.NEEDS_REVIEW, *rules.VERIFIED, *rules.STRICT)}
    unmet = {f"{r.id}_unmet" for r in (*rules.VERIFIED, *rules.STRICT)}
    fixed = {"NR97_facts_invalid", "NR98_engine_error", "NR99_conditions_not_met"}
    assert ids | unmet | fixed | set(RETIRED) <= set(TEMPLATES)
    assert {"U01_speaker_thread_not_checked", "V07_digits_match", "NR99_nothing_decided"} <= set(RETIRED), "old decisions render"
    assert all(s and "safe" not in s.lower() for s in TEMPLATES.values())
    assert Decision("verified", ("U03_offered_number_source_not_checked",), "decision-0.1.0-skeleton", "0" * 64, None).explanation


def test_facts_from_stored_answers():
    answers = [
        {"slot": "recording_consent", "value": "yes"}, {"slot": "company_named", "value": "Acme"},
        {"slot": "speaker_name", "value": "Jordan Lee"}, {"slot": "change_requested", "value": "yes"},
        {"slot": "change_requested", "value": "no"},           # changed their answer: both are kept
        {"slot": "flag", "value": "pressure_to_approve"},
    ]
    f = facts_from_answers(answers, call_result="answers_recorded", dialed_eligible_number=True)
    assert f.change_requested == ("yes", "no") and f.speaker_named and f.flags == ("pressure_to_approve",)
    assert (f.consent_first, f.answers_in_order, f.same_person_yes_and_digits) == (None, None, None), "no turns: unknown"
    assert decide(f).rules_fired == ("NV01_vendor_denied_change",), "any 'no' decides"


def test_an_earlier_call_counts_against_the_change_never_for_it():
    said_no = replace(GOOD, earlier_calls=(EarlierCall(result="failed", recording_consent=("yes",),
                                                       change_requested=("yes", "no")),))
    assert decide(said_no).rules_fired == ("NV01_vendor_denied_change",)
    old = replace(GOOD, earlier_calls=(EarlierCall(result="failed", recording_consent=("yes",),
                                                   last_four_result="matches_old_account"),))
    assert decide(old).rules_fired == ("NV03_digits_match_old_account",)
    clean = replace(GOOD, earlier_calls=(EarlierCall(result="failed", recording_consent=("yes",)),))
    assert decide(clean).outcome == "verified", "a clean dropped call neither helps nor hurts"


TRI = st.sampled_from([None, True, False])
CHANGES = {
    "call_result": st.sampled_from(["answers_recorded", "no_answer", "voicemail", "failed"]),
    "dialed_eligible_number": st.booleans(),
    "recording_consent": st.lists(st.sampled_from(["yes", "no"]), max_size=2).map(tuple),
    "change_requested": st.lists(st.sampled_from(["yes", "no", "unsure"]), max_size=2).map(tuple),
    "speaker_named": st.booleans(),
    "speaker_role": st.lists(st.sampled_from(["AR manager", "AP clerk", ""]), max_size=2).map(tuple),
    "flags": st.lists(st.sampled_from(["opt_out", "other", "pressure_to_approve", "speaker_changed"]), max_size=2).map(tuple),
    "offered_number": st.booleans(),
    "last_four_result": st.sampled_from([None, "match", "mismatch", "near_miss", "not_provided", "ambiguous"]),
    "checks": st.sampled_from([None, CLEAN, replace(CLEAN, answers_grounded=False), replace(CLEAN, speech_guard_trips=1)]),
    "earlier_calls": st.lists(st.builds(EarlierCall, result=st.just("failed"),
                                        recording_consent=st.sampled_from([(), ("yes",), ("no",)]),
                                        change_requested=st.lists(st.sampled_from(["yes", "no"]), max_size=1).map(tuple)),
                              max_size=1).map(tuple),
    "answered_by": st.sampled_from([None, "human", "machine"]),
    **{name: TRI for name in rules.TRI_STATE},
}
FACTS = st.lists(st.sampled_from(sorted(CHANGES)), max_size=2, unique=True).flatmap(
    lambda names: st.fixed_dictionaries({n: CHANGES[n] for n in names})).map(lambda changes: replace(GOOD, **changes))


@given(FACTS, st.booleans(), st.sampled_from(["call", "manual"]))
def test_decide_is_total_and_never_verifies_on_what_it_didnt_check(facts, strict, source):
    d = decide(facts, strict=strict, source=source)
    assert d.outcome in ("verified", "not_verified", "needs_review") and d.rules_fired
    if d.outcome == "verified":
        mode = rules.Mode(strict=strict, source=source)
        assert all(c.test(facts, mode) for c in (*rules.VERIFIED, *(rules.STRICT if strict else ())))
        assert facts.last_four_result == "match" and set(facts.change_requested) == {"yes"} and source == "call"
        assert facts.same_person_yes_and_digits is True and facts.dialed_out is True and facts.dialed_number_is_pick is True
        assert not any("no" in c.change_requested for c in facts.earlier_calls if rules.consented(c))
        assert facts.company_matches is not False, "a refusal in the company answer never verifies (NR03)"
    assert not set(d.rules_fired) & set(RETIRED), "a retired id is never recorded again"


def test_the_property_reaches_verified():
    """The property's Verified branch is live: one harmless change still verifies."""
    assert decide(replace(GOOD, line_risk=False)).outcome == "verified"
    assert decide(replace(GOOD, offered_number_from_request=False)).outcome == "verified"
