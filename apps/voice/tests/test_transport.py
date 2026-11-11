"""The text phone line: run_call drives policy.step() end to end with a scripted agent and a
scripted vendor, no Twilio. This is the same step() the Twilio adapter and the lab will call."""
from __future__ import annotations

import asyncio

import pytest
from dialback_decision import decide
from dialback_decision.facts import CallChecks, facts_from_answers
from dialback_decision.last_four import compare_last_four
from dialback_policy.script import REASK, Phase
from dialback_policy.step import AgentRequest
from dialback_voice.agent import ScriptedAgent
from dialback_voice.transport import ScriptedVendor, VendorScript, run_call

KEYPAD_ONLY_LINE = "Please don't say the digits. Enter them on your phone's keypad, then press the pound key."
NEW_LAST4, OLD_LAST4 = "4821", "4444"   # the account the change points to, and the one it replaces


def _run(make_ctx, clock, vs: VendorScript, **kw):
    return run_call(make_ctx(), ScriptedAgent(), ScriptedVendor(vs), clock=clock(), **kw)


def _by_slot(result):
    return {a["slot"]: a for a in result.answers["answers"]}


def _decide(result):
    """decide() on this call, with the facts the core adds from outside the call declared as a clean
    call has them, and the last four compared the way the core compares them (in memory)."""
    answers = result.answers["answers"]
    typed = [a["value"] for a in answers if a["slot"] == "account_last4"]
    facts = facts_from_answers(answers, call_result=result.end.result, checks=CallChecks(**result.checks),
                               dialed_out=True, dialed_eligible_number=True, dialed_number_is_pick=True,
                               last_four_result=compare_last_four(typed, NEW_LAST4, OLD_LAST4),
                               company_matches=True, answered_by="human", speaker_in_change_thread=False)
    return decide(facts)


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


def test_spoken_last_four_is_never_recorded(make_ctx, clock):
    # Spec 0043, D1 and D4: spoken digits are asked for on the keypad once, then the call finishes
    # with no digits (NR06), keeping every other answer. Nothing is spoken back.
    r = _run(make_ctx, clock, VendorScript(last4="4821", last4_mode="speech"))
    a = _by_slot(r)
    assert "account_last4" not in a and a["change_requested"]["value"] == "yes"
    assert r.end.result == "answers_recorded"
    agent_lines = [t.text for t in r.transcript if t.speaker == "agent"]
    assert agent_lines.count(f"{REASK} {KEYPAD_ONLY_LINE}") == 1
    assert r.checks["answers_grounded"] is True
    assert r.checks["agent_spoke_account_digits"] is False
    decision = _decide(r)
    assert decision.outcome == "needs_review" and "NR06_digits_not_provided" in decision.rules_fired


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


# --- spec 0043 ------------------------------------------------------------------------------

def _ask_double(phase: Phase, said: str):
    request = AgentRequest(phase=phase, goal="the goal", wording="the wording", next_goal=None,
                           customer_name="Northwind Traders", vendor_name="Acme Fasteners",
                           callback_number="(415) 555-0100",
                           transcript=((0, "agent", "A question."), (1, "callee", said)))
    return ScriptedAgent().reply(request)


def _double_value(phase: Phase, said: str) -> str:
    reply = _ask_double(phase, said)
    if reply.unclear:
        assert reply.tool_calls == () and reply.text is None   # unclear is never an answer (D6)
        return "unclear"
    assert len(reply.tool_calls) == 1 and reply.tool_calls[0].args["turn_id"] == 1
    return reply.tool_calls[0].args["value"]


@pytest.mark.parametrize(("phase", "said", "expected"), [
    (Phase.CONSENT, "Sure, no problem.", "yes"),
    (Phase.CHANGE, "We did.", "yes"),
    (Phase.CONSENT, "Hello?", "unclear"),
    (Phase.CHANGE, "Hello?", "unclear"),
    (Phase.CONSENT, "Yes, that's fine.", "yes"),
    (Phase.CONSENT, "No, I'd rather not.", "no"),
    (Phase.CONSENT, "I'm not sure.", "unclear"),             # consent has no unsure
    (Phase.CHANGE, "I'm not sure.", "unsure"),
    (Phase.CHANGE, "No, we didn't.", "no"),
    (Phase.LAST4, "I'd rather not say.", "refused"),
    (Phase.LAST4, "I don't know them.", "dont_know"),
    (Phase.LAST4, "I don't have that handy.", "dont_know"),   # the scripted vendor's dont_know line
    (Phase.LAST4, "Hmm, hang on.", "unclear"),
])
def test_double_reads_yes_no_like_the_policy(phase, said, expected):
    from dialback_policy.step import read_yes_no

    assert _double_value(phase, said) == expected
    if phase is not Phase.LAST4:
        assert _double_value(phase, said) == read_yes_no(said, allow_unsure=phase is Phase.CHANGE)


@pytest.mark.parametrize("phase", [Phase.COMPANY, Phase.DEPARTMENT, Phase.NAME, Phase.ROLE])
@pytest.mark.parametrize("said", ["unclear", "Unclear.", "  unclear "])
def test_the_double_records_a_free_text_unclear_as_said(phase, said):
    # R9: the caller's words are never a control signal; only a judgment question can be unclear.
    import datetime as dt

    from dialback_policy import step as policy
    from dialback_voice.transport import _ask

    assert _double_value(phase, said) == said.strip()
    request = AgentRequest(phase=phase, goal="the goal", wording="the wording", next_goal=None,
                           customer_name="Northwind Traders", vendor_name="Acme Fasteners",
                           callback_number="(415) 555-0100",
                           transcript=((0, "agent", "A question."), (1, "callee", said)))
    event = _ask(ScriptedAgent(), request, dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC))
    assert isinstance(event, policy.AgentReplied)             # never AgentUnclear


def test_clean_keypad_call_verifies(make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4=NEW_LAST4, last4_mode="keypad"))
    assert r.end.result == "answers_recorded" and _by_slot(r)["account_last4"]["capture_method"] == "keypad"
    assert _decide(r).outcome == "verified"


class _QuietSocket:
    """A live call where the caller never says or presses anything after the setup."""

    def __init__(self) -> None:
        self._setup_sent = False
        self.sent: list[str] = []

    async def receive_json(self) -> dict:
        if not self._setup_sent:
            self._setup_sent = True
            return {"type": "setup"}
        await asyncio.sleep(60)                          # the line stays open and quiet
        raise ConnectionError("the test ran too long")

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj["token"])

    async def close(self) -> None: ...


def test_a_silent_vendor_gets_the_check_in_then_the_end(make_ctx, clock):
    # Spec 0051, D8: the text line's silent vendor goes through the same policy rules as a quiet
    # live call: the check-in, then, with no reply, the end.
    from dialback_voice.twilio import relay

    ctx = make_ctx()
    text = run_call(ctx, ScriptedAgent(), ScriptedVendor(VendorScript(silent=True)), clock=clock())
    lines = [t.text for t in text.transcript if t.speaker == "agent"]
    assert lines == [ctx.script.disclosure(), ctx.script.consent_question(), "Sorry, are you still there?",
                     ctx.script.goodbye()]
    assert (text.end.result, text.end.reason) == ("failed", "silence") and text.answers["answers"] == []

    ws = _QuietSocket()
    no_estimate = relay.SpeechEstimate(lead=0.0, chars_per_second=1e9, margin=0.0)   # short, for the test
    live = asyncio.run(relay.run_relay(ctx, ScriptedAgent(), ws, verify=lambda setup: True, clock=clock(),
                                       greeting=ctx.script.disclosure(), quiet_seconds=0.1,
                                       after_check_in_seconds=0.1, estimate=no_estimate))
    assert [t.text for t in live.transcript if t.speaker == "agent"] == lines
    assert ws.sent == lines[1:]                          # the welcome greeting spoke the disclosure
    assert (live.end.result, live.end.reason) == (text.end.result, text.end.reason)
    assert live.answers["answers"] == []


@pytest.mark.parametrize(("typed", "rule"), [("1234", "NV02_digits_mismatch"),
                                             (OLD_LAST4, "NV03_digits_match_old_account")])
def test_a_wrong_typed_four_is_still_not_verified(typed, rule, make_ctx, clock):
    r = _run(make_ctx, clock, VendorScript(last4=typed, last4_mode="keypad"))
    decision = _decide(r)
    assert decision.outcome == "not_verified" and rule in decision.rules_fired
