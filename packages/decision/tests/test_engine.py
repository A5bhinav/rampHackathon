from dataclasses import replace

from dialback_decision import CallChecks, CaseFacts, decide
from dialback_decision.engine import ENGINE_VERSION, TEMPLATES
from dialback_decision.facts import facts_from_answers
from hypothesis import given
from hypothesis import strategies as st

CLEAN = CallChecks(disclosure_first=True, answers_grounded=True, agent_spoke_account_digits=False,
                   new_number_accepted=False, policy_violations=0, speech_guard_trips=0)
GOOD = CaseFacts(call_result="answers_recorded", dialed_eligible_number=True, recording_consent="yes",
                 company_named="Acme Fasteners", change_requested="yes", speaker_named=True,
                 last_four_result="match", company_matches=True, checks=CLEAN)


def test_a_clean_confirmation_is_verified_and_says_what_was_not_checked():
    d = decide(GOOD)
    assert d.outcome == "verified" and d.engine_version == ENGINE_VERSION
    assert "V07_digits_match" in d.rules_fired
    assert {"U01_speaker_thread_not_checked", "U02_same_person_not_checked"} <= set(d.rules_fired)
    assert any(s.startswith("Not checked by this engine version") for s in d.explanation)
    assert all("safe" not in s.lower() for s in d.explanation)


def test_not_verified_beats_everything_else():
    d = decide(replace(GOOD, change_requested="no", flags=("pressure_to_approve",)))
    assert d.outcome == "not_verified" and d.rules_fired == ("NV01_vendor_denied_change",)
    assert decide(replace(GOOD, last_four_result="mismatch")).outcome == "not_verified"
    assert decide(replace(GOOD, last_four_result="matches_old_account")).outcome == "not_verified"
    assert decide(replace(GOOD, offered_number_from_request=True)).outcome == "not_verified"


def test_each_review_condition_sends_the_case_to_a_person():
    variants = [
        replace(GOOD, call_result="voicemail"), replace(GOOD, recording_consent="no"), replace(GOOD, company_matches=False),
        replace(GOOD, speaker_in_change_thread=True), replace(GOOD, last_four_result="near_miss"),
        replace(GOOD, last_four_result="not_provided"), replace(GOOD, last_four_result="ambiguous"),
        replace(GOOD, last_four_result="inconsistent"), replace(GOOD, change_requested="unsure"),
        replace(GOOD, flags=("opt_out",)), replace(GOOD, offered_number=True),
        replace(GOOD, checks=replace(CLEAN, answers_grounded=False)), replace(GOOD, checks=replace(CLEAN, disclosure_first=False)),
        replace(GOOD, checks=replace(CLEAN, policy_violations=1)), replace(GOOD, same_person_yes_and_digits=False),
    ]
    for facts in variants:
        assert decide(facts).outcome == "needs_review", facts


def test_missing_facts_fail_closed():
    assert decide(replace(GOOD, checks=None)).outcome == "needs_review"
    assert decide(replace(GOOD, company_matches=None)).rules_fired == ("NR99_nothing_decided",)
    assert decide(replace(GOOD, recording_consent=None)).outcome == "needs_review"
    assert decide(replace(GOOD, dialed_eligible_number=False)).outcome == "needs_review"


def test_inputs_hash_is_stable_and_covers_every_fact():
    assert decide(GOOD).inputs_hash == decide(replace(GOOD)).inputs_hash
    assert decide(replace(GOOD, flags=("a", "b"))).inputs_hash == decide(replace(GOOD, flags=("b", "a"))).inputs_hash
    assert decide(GOOD).inputs_hash != decide(replace(GOOD, speaker_named=False)).inputs_hash


def test_every_fired_rule_has_a_sentence():
    for facts in (GOOD, replace(GOOD, change_requested="no"), replace(GOOD, call_result="busy"), replace(GOOD, checks=None)):
        assert all(r in TEMPLATES for r in decide(facts).rules_fired)


def test_facts_from_stored_answers():
    answers = [
        {"slot": "recording_consent", "value": "yes"}, {"slot": "company_named", "value": "Acme"},
        {"slot": "speaker_name", "value": "Jordan Lee"}, {"slot": "change_requested", "value": "yes"},
        {"slot": "change_requested", "value": "no"},           # changed their answer: unknown, not yes
        {"slot": "flag", "value": "pressure_to_approve"},
    ]
    f = facts_from_answers(answers, call_result="answers_recorded", dialed_eligible_number=True)
    assert f.change_requested is None and f.speaker_named and f.flags == ("pressure_to_approve",)


@given(st.builds(CaseFacts, call_result=st.sampled_from(["answers_recorded", "no_answer", "voicemail"]),
                 dialed_eligible_number=st.booleans(),
                 recording_consent=st.sampled_from([None, "yes", "no"]),
                 change_requested=st.sampled_from([None, "yes", "no", "unsure"]),
                 last_four_result=st.sampled_from([None, "match", "mismatch", "near_miss", "not_provided"]),
                 company_matches=st.sampled_from([None, True, False]),
                 flags=st.lists(st.sampled_from(["opt_out", "other", "pressure_to_approve"]), max_size=2).map(tuple)))
def test_decide_is_total_and_never_verifies_without_matching_digits(facts):
    d = decide(facts)
    assert d.outcome in ("verified", "not_verified", "needs_review")
    if d.outcome == "verified":
        assert facts.last_four_result == "match" and facts.change_requested == "yes"
