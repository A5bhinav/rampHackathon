"""The text phone line: run_call drives policy.step() end to end with a scripted agent and a
scripted vendor, no Twilio. This is the same step() the Twilio adapter and the lab will call."""
from __future__ import annotations

from dialback_decision import decide
from dialback_decision.facts import CallChecks, facts_from_answers
from dialback_policy.script import Phase
from dialback_voice.agent import ScriptedAgent
from dialback_voice.transport import ScriptedVendor, VendorScript, run_call


def _run(make_ctx, clock, vs: VendorScript, **kw):
    return run_call(make_ctx(), ScriptedAgent(), ScriptedVendor(vs), clock=clock(), **kw)


def _by_slot(result):
    return {a["slot"]: a for a in result.answers["answers"]}


def test_cooperative_keypad_call_confirms_and_checks_pass(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4="4821", last4_mode="keypad"))
    assert r.end.result == "answers_recorded"
    assert r.end.reason == "completed"
    a = _by_slot(r)
    assert a["recording_consent"]["value"] == "yes"
    assert a["company_named"]["value"] == "Acme Fasteners"
    assert a["change_requested"]["value"] == "yes"
    assert a["account_last4"]["value"] == "4821"
    assert a["account_last4"]["capture_method"] == "keypad"
    assert r.checks == {
        "disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
        "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0,
    }


def test_disclosure_is_the_first_agent_line_and_digits_never_spoken(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4="4821", last4_mode="keypad"))
    agent_lines = [t.text for t in r.transcript if t.speaker == "agent"]
    assert agent_lines[0] == make_ctx().script.disclosure()
    assert "4821" not in " ".join(agent_lines)
    assert "[keypad]" in [t.text for t in r.transcript if t.speaker == "callee"]  # digits off the transcript


def test_spoken_last_four_is_grounded_and_never_spoken_back(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4="4821", last4_mode="speech"))
    a = _by_slot(r)
    assert a["account_last4"]["value"] == "4821"
    assert a["account_last4"]["capture_method"] == "speech"
    assert r.checks["answers_grounded"] is True
    assert r.checks["agent_spoke_account_digits"] is False


def test_consent_no_records_and_sends_to_review(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(consent="no"))
    assert r.end.result == "answers_recorded"
    assert r.end.reason == "consent_refused"
    a = _by_slot(r)
    assert a["recording_consent"]["value"] == "no"
    assert "company_named" not in a  # nothing is asked after a consent refusal


def test_hangup_before_consent_is_declined(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(hang_up_at=Phase.CONSENT))
    assert r.end.result == "declined"


def test_machine_gets_one_voicemail(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(answered_by="machine"))
    assert r.end.result == "voicemail"


def test_a_clean_call_records_name_and_role_separately(make_ctx, clock):
    # Spec 0034, B5: name and role are two questions, each answer recorded as said on its own turn,
    # so a clean call carries the speaker_role V06 needs and can reach verified.
    r = _run(make_ctx, clock, VendorScript(name="Jordan Lee", role="AP clerk"))
    agent_lines = [t.text for t in r.transcript if t.speaker == "agent"]
    assert "May I have your name?" in agent_lines and "And what's your role there?" in agent_lines
    answers = r.answers["answers"]
    name = [a for a in answers if a["slot"] == "speaker_name"]
    role = [a for a in answers if a["slot"] == "speaker_role"]
    assert [a["value"] for a in name] == ["Jordan Lee"] and [a["value"] for a in role] == ["AP clerk"]
    assert name[0]["turn_id"] != role[0]["turn_id"]
    assert r.checks["answers_grounded"] is True
    # The facts the core adds from outside the call (the dialed pick, the keyed last-four comparison)
    # are declared as a clean call has them; the rest come from this call's own answers and checks.
    facts = facts_from_answers(answers, call_result=r.end.result, checks=CallChecks(**r.checks), dialed_out=True,
                               dialed_eligible_number=True, dialed_number_is_pick=True, last_four_result="match",
                               company_matches=True, answered_by="human", speaker_in_change_thread=False)
    assert decide(facts).outcome == "verified"


def test_refused_last_four_still_completes(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4_mode="refuse"))
    a = _by_slot(r)
    assert a["account_last4"]["value"] == "refused"
    assert r.end.result == "answers_recorded"
