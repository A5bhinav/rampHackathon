"""One test per rule in the table (spec 0006). Each case changes the clean call in one way and
checks that the rule fires in its block, or, for a Verified condition, that it holds on the
clean call and fails on the change. A last test checks every rule in the table has a case."""
from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import CLEAN, GOOD
from dialback_decision import decide, rules
from dialback_decision.facts import EarlierCall

# rule id -> (changes to the clean call, decide() keywords, expected outcome)
FIRES = {
    "NR97_facts_invalid": ({"flags": ("approve_now",)}, {}, "needs_review"),
    "NR01_no_live_person": ({"answered_by": "machine"}, {}, "needs_review"),
    "NR02_no_recording_consent": ({"recording_consent": ("yes", "no")}, {}, "needs_review"),
    "NV01_vendor_denied_change": ({"change_requested": ("no",)}, {}, "not_verified"),
    "NV02_digits_mismatch": ({"last_four_result": "mismatch"}, {}, "not_verified"),
    "NV03_digits_match_old_account": ({"last_four_result": "matches_old_account"}, {}, "not_verified"),
    "NV04_offered_number_from_request": ({"offered_number": True, "offered_number_from_request": True}, {}, "not_verified"),
    "NR03_wrong_company": ({"company_matches": False}, {}, "needs_review"),
    "NR04_speaker_from_change_thread": ({"speaker_in_change_thread": True}, {}, "needs_review"),
    "NR05_digits_near_miss": ({"last_four_result": "near_miss"}, {}, "needs_review"),
    "NR06_digits_not_provided": ({"last_four_result": "not_provided"}, {}, "needs_review"),
    "NR07_digits_ambiguous": ({"last_four_result": "ambiguous"}, {}, "needs_review"),
    "NR08_digits_inconsistent": ({"last_four_result": "inconsistent"}, {}, "needs_review"),
    "NR09_change_unsure": ({"change_requested": ("unsure",)}, {}, "needs_review"),
    "NR10_pressure": ({"flags": ("pressure_to_approve",)}, {}, "needs_review"),
    "NR11_opt_out": ({"flags": ("opt_out",)}, {}, "needs_review"),
    "NR12_asked_what_is_checked": ({"flags": ("asked_what_is_checked",)}, {}, "needs_review"),
    "NR13_another_number": ({"offered_number": True}, {}, "needs_review"),
    "NR14_answer_not_in_transcript": ({"answers_in_order": False}, {}, "needs_review"),
    "NR15_script_violation": ({"checks": replace(CLEAN, speech_guard_trips=1)}, {}, "needs_review"),
    "NR16_other_flag": ({"flags": ("other",)}, {}, "needs_review"),
    "NR17_split_speakers": ({"flags": ("speaker_changed",)}, {}, "needs_review"),
    "NR18_contradictory_answers": ({"speaker_role": ("AR manager", "AP clerk")}, {}, "needs_review"),
    "NR19_earlier_call_concern": ({"earlier_calls": (EarlierCall(result="failed", flags=("opt_out",)),)}, {}, "needs_review"),
    "NR20_manual_answers": ({}, {"source": "manual"}, "needs_review"),
    "NR21_line_risk": ({"line_risk": True}, {}, "needs_review"),
    "NR99_conditions_not_met": ({"dialed_out": None}, {}, "needs_review"),
}

# More ways each rule fires, so every clause of a rule is exercised.
ALSO = [
    ("NR97_facts_invalid", {"call_result": "dropped"}), ("NR97_facts_invalid", {"dialed_out": "yes"}),
    ("NR97_facts_invalid", {"earlier_calls": (EarlierCall(result="failed", change_requested=("maybe",)),)}),
    ("NR01_no_live_person", {"call_result": "voicemail"}),
    ("NR02_no_recording_consent", {"recording_consent": ("no",)}), ("NR02_no_recording_consent", {"consent_first": False}),
    ("NV01_vendor_denied_change", {"earlier_calls": (EarlierCall(result="failed", change_requested=("no",)),)}),
    ("NV02_digits_mismatch", {"earlier_calls": (EarlierCall(result="failed", last_four_result="mismatch"),)}),
    ("NR13_another_number", {"flags": ("asked_to_call_another_number",)}),
    ("NR14_answer_not_in_transcript", {"checks": replace(CLEAN, answers_grounded=False)}),
    ("NR15_script_violation", {"checks": replace(CLEAN, policy_violations=1)}),
    ("NR15_script_violation", {"checks": replace(CLEAN, disclosure_first=False)}),
    ("NR17_split_speakers", {"same_person_yes_and_digits": False}),
    ("NR18_contradictory_answers", {"company_named": ("Acme Fasteners", "Acme Supplies")}),
    ("NR19_earlier_call_concern", {"earlier_calls": (EarlierCall(result="failed", last_four_result="near_miss"),)}),
    ("NR19_earlier_call_concern", {"earlier_calls": (EarlierCall(result="failed", recording_consent=("no",)),)}),
]

# Verified condition -> a change that fails it alone.
UNMET = {
    "V01_number_eligible": {"dialed_eligible_number": False},
    "V02_outbound_dial": {"dialed_number_is_pick": None},
    "V03_live_person": {"answered_by": None},
    "V04_recording_consent": {"recording_consent": ()},
    "V05_company_matches": {"company_matches": None},
    "V06_speaker_identified": {"speaker_role": ()},
    "V07_right_person_confirmed": {"speaker_in_change_thread": None},
    "V08_digits_match": {"last_four_result": None},
    "V09_same_person": {"same_person_yes_and_digits": None},
    "V10_answers_grounded": {"answers_in_order": None},
    "V11_script_clean": {"checks": None},
    "V12_no_flags": {"flags": ("other",)},
    "S01_known_contact": {"speaker_known_before_request": None},
}


@pytest.mark.parametrize("rule_id", sorted(FIRES))
def test_each_rule_fires_in_its_block(rule_id):
    changes, kwargs, outcome = FIRES[rule_id]
    d = decide(replace(GOOD, **changes), **kwargs)
    assert d.outcome == outcome and rule_id in d.rules_fired, d.rules_fired


@pytest.mark.parametrize("rule_id, changes", ALSO, ids=[f"{r}-{i}" for i, (r, _) in enumerate(ALSO)])
def test_each_clause_of_a_rule_fires(rule_id, changes):
    assert rule_id in decide(replace(GOOD, **changes)).rules_fired


@pytest.mark.parametrize("rule_id", sorted(UNMET))
def test_each_verified_condition_holds_on_the_clean_call_and_fails_alone(rule_id):
    condition = {r.id: r for r in (*rules.VERIFIED, *rules.STRICT)}[rule_id]
    mode = rules.Mode(strict=True, source="call")
    assert condition.test(GOOD, mode)
    assert not condition.test(replace(GOOD, **UNMET[rule_id]), mode)


def test_an_unmet_condition_is_named_in_the_fall_through():
    d = decide(replace(GOOD, dialed_eligible_number=False, same_person_yes_and_digits=None))
    assert d.rules_fired == ("NR99_conditions_not_met", "V01_number_eligible_unmet", "V09_same_person_unmet")


def test_strict_mode_adds_one_condition():
    standard, strict = decide(GOOD), decide(GOOD, strict=True)
    assert standard.outcome == strict.outcome == "verified"
    assert strict.rules_fired == (*standard.rules_fired, "S01_known_contact")
    assert decide(replace(GOOD, line_risk=None), strict=True).outcome == "verified", "an unknown line-risk signal doesn't block"


def test_every_rule_in_the_table_has_a_case():
    table = {r.id for r in (*rules.BLOCK0, *rules.NOT_VERIFIED, *rules.NEEDS_REVIEW)}
    assert table | {"NR97_facts_invalid", "NR99_conditions_not_met"} == set(FIRES)
    assert {r.id for r in (*rules.VERIFIED, *rules.STRICT)} == set(UNMET)
    assert len(rules.NOT_VERIFIED) == 4 and len(rules.VERIFIED) == 12
    assert len(rules.NEEDS_REVIEW) + len(rules.BLOCK0) == 21, "NR01-NR21"
