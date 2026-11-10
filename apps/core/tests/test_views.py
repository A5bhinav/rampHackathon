"""The decision's checklist and rule groups (spec 0035): read from the rules the engine recorded,
never re-run. Unit tests, no database."""
from __future__ import annotations

from dialback_core.views import answer_view, calls, checklist, effect, fired
from dialback_decision import rules
from dialback_decision.engine import ENGINE_VERSION

V = [r.id for r in rules.VERIFIED]


def test_verified_passes_every_condition():
    items, note = checklist("verified", V, ENGINE_VERSION)
    assert [i["status"] for i in items] == ["passed"] * 11 and [i["id"] for i in items] == V
    assert note.startswith("Every condition")


def test_unmet_conditions_are_exactly_the_ones_recorded():
    items, _ = checklist("needs_review", [rules.NOT_MET, "V08_digits_match_unmet", "V12_no_flags_unmet"], ENGINE_VERSION)
    status = {i["id"]: i["status"] for i in items}
    assert [k for k, v in status.items() if v == "not_met"] == ["V08_digits_match", "V12_no_flags"]
    assert sum(v == "passed" for v in status.values()) == 9, "11 conditions since V05 was retired (spec 0082, D7b)"
    v08 = next(i for i in items if i["id"] == "V08_digits_match")
    assert v08["sentence"] == "The person did not give matching digits for the new account."


def test_strict_mode_adds_s01():
    items, _ = checklist("verified", [*V, "S01_known_contact"], ENGINE_VERSION)
    assert items[-1] == {"id": "S01_known_contact", "status": "passed",
                         "sentence": "The person is a contact known from before the change request."}


def test_an_earlier_block_means_not_checked_with_its_reason():
    for outcome, fired_ids, word in (("not_verified", ["NV02_digits_mismatch"], "Not verified"),
                                     ("needs_review", ["NR10_pressure"], "Needs review"),
                                     ("needs_review", ["NR01_no_live_person"], "input"),
                                     ("needs_review", [rules.ENGINE_ERROR], "error")):
        items, note = checklist(outcome, fired_ids, ENGINE_VERSION)
        assert {i["status"] for i in items} == {"not_checked"}, fired_ids
        assert word in note, (fired_ids, note)


def test_an_older_engine_version_is_never_read_as_passed():
    items, note = checklist("verified", ["V01_eligible_number_dialed"], "decision-0.9.0")
    assert {i["status"] for i in items} == {"not_checked"} and "decision-0.9.0" in note


def test_fired_rules_name_their_block():
    assert [f["block"] for f in fired([rules.NOT_MET, "V08_digits_match_unmet", "NR03_wrong_company", "NV01_vendor_denied_change",
                                       "NR02_no_recording_consent", "V01_number_eligible", "V02_live_person", "made_up"])] \
        == ["not_met", "not_met", "needs_review", "not_verified", "input", "verified", "retired", "retired"]
    assert fired(["made_up"])[0]["sentence"] == "made_up"


def test_a_retired_company_condition_still_renders():
    # A decision-1.0.0 decision recorded V05; spec 0082 D7b retired it, and it still renders with its sentence.
    rows = fired(["V05_company_matches", "V05_company_matches_unmet"])
    assert [r["block"] for r in rows] == ["retired", "retired"]
    assert [r["sentence"] for r in rows] == ["The person named this vendor.",
                                             "The person did not clearly name this vendor."]


def test_effects_say_what_happens_to_the_hold():
    assert "hold stays on" in effect("verified") and "approver" in effect("verified")
    assert "blocked" in effect("not_verified") and "review" in effect("needs_review")
    assert "hold stays on" in effect("something new")


def test_an_answer_never_carries_the_digits_hash():
    stored = {"slot": "account_last4", "turn_id": 9, "tool_call_id": "t", "captured_at": "x", "capture_method": "keypad",
              "value": {"digits_hmac": "ab" * 32, "digit_count": 4, "key_version": 1}}
    view = answer_view(stored)
    assert (view["value"], view["digit_count"]) == (None, 4) and "digits_hmac" not in str(view)
    assert answer_view({**stored, "value": "refused"})["value"] == "refused"
    assert answer_view({**stored, "value": {"digit_count": 9}})["digit_count"] is None, "out-of-range counts are dropped"
    offered = answer_view({**stored, "slot": "offered_number", "value": "+14155550100"})
    assert "NR13_another_number" in offered["feeds"] and offered["value"] == "A phone number (not shown)"
    assert answer_view({**stored, "value": "1234"})["value"] is None, "no string but dont_know / refused passes for the digits"


def test_invalid_facts_and_earlier_denials_mean_not_checked():
    items, note = checklist("needs_review", [rules.INVALID], ENGINE_VERSION)
    assert {i["status"] for i in items} == {"not_checked"} and "unreadable" in note
    items, note = checklist("not_verified", ["NV01_vendor_denied_change"], ENGINE_VERSION)  # an earlier consented "no"
    assert {i["status"] for i in items} == {"not_checked"} and "Not verified" in note


def test_strict_mode_keeps_s01_when_another_condition_fails():
    items, _ = checklist("needs_review", [rules.NOT_MET, "V08_digits_match_unmet"], ENGINE_VERSION, strict=True)
    assert items[-1]["id"] == "S01_known_contact" and items[-1]["status"] == "passed"
    assert next(i for i in items if i["id"] == "V08_digits_match")["status"] == "not_met"


def test_unknown_changer_needs_two_approvers():
    assert "two different approvers" in effect("verified", unknown_changer=True)


def _ans(slot, value, turn):
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"t{turn}", "captured_at": "x", "capture_method": "speech"}


def test_attempts_from_evidence_a_timeout_and_a_redial():
    first, second = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    events = [
        ("call_started", "t1", {"call_id": first, "attempt": 1}),
        ("call_ended", "t2", {"call_id": first, "attempt": 1, "result": "callback_requested",
                              "answers": {"answers": [_ans("recording_consent", "yes", 1), _ans("change_requested", "yes", 3)]}}),
        ("call_started", "t3", {"call_id": second, "attempt": 2}),
        ("call_ended", "t4", {"call_id": second, "attempt": 2, "result": "answers_recorded", "answered_by": "human",
                              "answers": {"answers": [_ans("recording_consent", "yes", 1), _ans("change_requested", "yes", 5)]}}),
        ("call_started", "t5", {"call_id": "33333333-3333-4333-8333-333333333333", "attempt": 3}),
        ("call_timeout", "t6", {"attempt": 3}),  # no call_id: closes the open attempt
    ]
    one, two, three = calls(events, {second: {"provider": "twilio"}})
    assert (one["result"], two["result"], three["result"]) == ("callback_requested", "answers_recorded", "timed_out")
    assert three["ended_at"] == "t6" and "a person must review" in three["reason"] and two["provider"] == "twilio"
    early = {a["slot"]: a["feeds"] for a in one["answers"]}
    assert early["change_requested"] == ["NV01_vendor_denied_change", "NR19_earlier_call_concern"], "an earlier call counts only against"
    assert "V07_right_person_confirmed" in {a["slot"]: a["feeds"] for a in two["answers"]}["change_requested"]
    assert calls([("call_ended", "t", {"call_id": first, "result": "failed"})], {}) == [], "no start, no attempt"
