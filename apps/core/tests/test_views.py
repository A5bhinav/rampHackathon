"""The decision's checklist and rule groups (spec 0035): read from the rules the engine recorded,
never re-run. Unit tests, no database."""
from __future__ import annotations

from dialback_core.views import answer_view, checklist, effect, fired
from dialback_decision import rules
from dialback_decision.engine import ENGINE_VERSION

V = [r.id for r in rules.VERIFIED]


def test_verified_passes_every_condition():
    items, note = checklist("verified", V, ENGINE_VERSION)
    assert [i["status"] for i in items] == ["passed"] * 12 and [i["id"] for i in items] == V
    assert note.startswith("Every condition")


def test_unmet_conditions_are_exactly_the_ones_recorded():
    items, _ = checklist("needs_review", [rules.NOT_MET, "V08_digits_match_unmet", "V12_no_flags_unmet"], ENGINE_VERSION)
    status = {i["id"]: i["status"] for i in items}
    assert [k for k, v in status.items() if v == "not_met"] == ["V08_digits_match", "V12_no_flags"]
    assert sum(v == "passed" for v in status.values()) == 10
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
    assert "NR13_another_number" in answer_view({**stored, "slot": "offered_number", "value": "+14155550100"})["feeds"]
